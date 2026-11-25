"""限量读取 multipart 上传，在入库前完成解析和校验。"""
from collections.abc import AsyncIterable
from email import policy
from email.parser import BytesParser

from anyio import fail_after, to_thread
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import (
    BusinessError,
    KnowledgeUploadInvalidError,
    KnowledgeUploadParseError,
    KnowledgeUploadSizeError,
    KnowledgeUploadTimeoutError,
    KnowledgeUploadTypeError,
)
from app.core.knowledge import (
    DOCUMENT_FILE_EXTENSIONS,
    DOCUMENT_FILE_MAX_BYTES,
    DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS,
    DOCUMENT_MULTIPART_OVERHEAD_BYTES,
    DOCUMENT_TITLE_LENGTH,
)
from app.core.logging import get_logger
from app.schemas.knowledge import KnowledgeDocumentCreate, KnowledgeDocumentItem
from app.services.knowledge import create_knowledge_document, require_owned_knowledge_base
from app.services.knowledge_file_text import extract_file_text

logger = get_logger(__name__)
MULTIPART_MEDIA_TYPE = "multipart/form-data"
UPLOAD_FIELD = "file"


# 做什么：在接收文件前确认库可见，并尽快释放事务与行锁
# 入参：请求的写入会话、登录用户和知识库编号
# 返回：无；库不可见时沿用 404/4001
def check_upload_owner(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    with db_session.begin():
        require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)


# 做什么：限量接收整个 multipart 请求，不启用框架的磁盘暂存
# 入参：由 ASGI 请求提供的字节流
# 返回：内存中的请求体；文件和表单头合计超限时立即停止
async def read_multipart_body(body: AsyncIterable[bytes]) -> bytearray:
    buffer = bytearray()
    max_body_bytes = DOCUMENT_FILE_MAX_BYTES + DOCUMENT_MULTIPART_OVERHEAD_BYTES
    async for chunk in body:
        if len(buffer) + len(chunk) > max_body_bytes:
            raise KnowledgeUploadSizeError()
        buffer.extend(chunk)
    return buffer


# 做什么：拆出名为 file 的唯一文件字段及原始字节
# 入参：Content-Type（含 boundary）与已限量的请求体
# 返回：浏览器提交的文件名和内容；封装损坏或非单文件时拒绝
def read_file_part(content_type: str, body: bytearray) -> tuple[str, bytes]:
    # 新手注意：multipart 像带标签的信封，标准库按边界拆开信封，不改变文件正文。
    headers = f"Content-Type: {content_type}\r\n\r\n".encode("ascii")
    message = BytesParser(policy=policy.default).parsebytes(headers + body)
    if message.get_content_type() != MULTIPART_MEDIA_TYPE or message.defects:
        raise KnowledgeUploadInvalidError()
    parts = list(message.iter_parts())
    if len(parts) != 1:
        raise KnowledgeUploadInvalidError()
    part = parts[0]
    filename = part.get_filename()
    field_name = part.get_param("name", header="content-disposition")
    if (part.defects or part.get_content_disposition() != "form-data"
            or field_name != UPLOAD_FIELD or not filename or part.is_multipart()
            or part.get("Content-Transfer-Encoding")):
        raise KnowledgeUploadInvalidError()
    return filename, part.get_payload(decode=True)


# 做什么：检查单文件的名称、类型、大小和解析结果
# 入参：multipart 内容和标题所需的文件名
# 返回：可交给现有纯文本入库函数的模型；无效数据抛 4xxx 错误
def parse_file_upload(content_type: str, body: bytearray) -> KnowledgeDocumentCreate:
    try:
        filename, file_bytes = read_file_part(content_type, body)
    except (ValueError, RecursionError) as error:
        raise KnowledgeUploadInvalidError() from error
    # 去掉浏览器可能带来的路径，只把最后一段文件名用作标题。
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    title, separator, extension = basename.rpartition(".")
    suffix = separator + extension.lower()
    if suffix not in DOCUMENT_FILE_EXTENSIONS:
        raise KnowledgeUploadTypeError()
    if len(file_bytes) > DOCUMENT_FILE_MAX_BYTES:
        raise KnowledgeUploadSizeError()
    content = extract_file_text(suffix, file_bytes)
    try:
        return KnowledgeDocumentCreate(title=title, content=content)
    except ValidationError as error:
        raise KnowledgeUploadInvalidError(f"文件名去掉扩展名后须为 1–{DOCUMENT_TITLE_LENGTH} 字，请重命名后上传") from error


# 做什么：给接收和解析设共同的时间上限，并屏蔽解析器内部细节
# 入参：请求类型和字节流，关联用户及知识库编号
# 返回：解析好的正文与标题；超时、封装错误和格式错误返回业务异常
async def prepare_file_upload(
    content_type: str, body: AsyncIterable[bytes], owner_id: int, knowledge_base_id: int,
) -> KnowledgeDocumentCreate:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != MULTIPART_MEDIA_TYPE or "\r" in content_type or "\n" in content_type:
        raise KnowledgeUploadInvalidError()
    try:
        with fail_after(DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS):
            request_body = await read_multipart_body(body)
            # 超时的同步解析可以自行结束，但无法触及数据库，也不会进入下一步入库。
            return await to_thread.run_sync(parse_file_upload, content_type, request_body, abandon_on_cancel=True)
    except TimeoutError as error:
        raise KnowledgeUploadTimeoutError() from error
    except BusinessError:
        raise
    except Exception as error:
        logger.warning("文件解析失败 | knowledge_base_id=%s | owner_id=%s | error_type=%s",
                       knowledge_base_id, owner_id, type(error).__name__)
        raise KnowledgeUploadParseError() from error


# 做什么：按归属、解析、写入的顺序完成文件上传
# 入参：数据库会话、用户与库编号、请求类型及文件流
# 返回：复用纯文本入库后的结果；解析失败时不创建文档
async def upload_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, content_type: str, body: AsyncIterable[bytes],
) -> KnowledgeDocumentItem:
    await to_thread.run_sync(check_upload_owner, db_session, owner_id, knowledge_base_id)
    payload = await prepare_file_upload(content_type, body, owner_id, knowledge_base_id)
    return await to_thread.run_sync(create_knowledge_document, db_session, owner_id, knowledge_base_id, payload)
