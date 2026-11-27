"""限量接收 multipart 文件到磁盘，再交给对应通道解析。"""
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile

from anyio import CancelScope, current_time, fail_after, to_thread
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
    DOCUMENT_FILE_MAX_BYTES,
    DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS,
    DOCUMENT_TITLE_LENGTH,
    DOCUMENT_VIDEO_MAX_BYTES,
    DOCUMENT_VIDEO_PARSE_TIMEOUT_SECONDS,
    DOCUMENT_VIDEO_SIZE_ERROR,
    FILE_EXTENSION_KINDS,
    FileKind,
)
from app.core.logging import get_logger
from app.schemas.knowledge import KnowledgeDocumentItem
from app.services.knowledge import require_owned_knowledge_base
from app.services.knowledge_channel_common import ParsedFile
from app.services.knowledge_channel_registry import parse_file, parse_video_file
from app.services.knowledge_document_store import create_file_knowledge_document
from app.services.knowledge_probe import TEXT_SAMPLE_BYTES, require_supported_file
from app.services.knowledge_upload_multipart import (
    MultipartFileWriter, iter_multipart_chunks, multipart_boundary, read_upload_header,
)

logger = get_logger(__name__)
FILENAME_SEPARATOR = "."
FILE_TEMP_PREFIX = "fengxuan-upload-"
VIDEO_TEMP_PREFIX = "fengxuan-video-"


@dataclass(frozen=True)
class UploadName:
    """从文件字段得到的原始名称、标题和小写后缀。"""

    basename: str
    title: str
    suffix: str


@dataclass(frozen=True)
class FileUploadPayload:
    """校验并解析好的上传文件：标题、正文切块、真实类型与原始信息。"""

    title: str
    parsed: ParsedFile
    file_kind: int
    file_name: str
    file_size: int


# 做什么：在接收文件前确认库可见，并尽快释放事务与行锁
# 入参：请求的写入会话、登录用户和知识库编号
# 返回：无；库不可见时沿用 404/4001
def check_upload_owner(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    with db_session.begin():
        require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)


# 做什么：丢弃浏览器传来的路径并拆出文件标题和后缀
# 入参：multipart 文件头中的文件名
# 返回：规范化的文件名；缺少后缀或标题无效时拒绝
def parse_upload_name(filename: str) -> UploadName:
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    title, separator, extension = basename.rpartition(FILENAME_SEPARATOR)
    if not separator or not extension:
        raise KnowledgeUploadTypeError("文件缺少扩展名，请补上 .txt、.pdf 之类的后缀再上传")
    if not title or len(title) > DOCUMENT_TITLE_LENGTH:
        raise KnowledgeUploadInvalidError(f"文件名去掉扩展名后须为 1–{DOCUMENT_TITLE_LENGTH} 字，请重命名后上传")
    return UploadName(basename, title, FILENAME_SEPARATOR + extension.lower())


# 做什么：探测小文件真实类型并交给使用字节的通道
# 入参：上传文件名和已经从磁盘读取的有限字节
# 返回：可交给文件文档入库的载荷；无效数据抛 4xxx 错误
def parse_file_upload(upload_name: UploadName, file_bytes: bytes) -> FileUploadPayload:
    file_kind = require_supported_file(upload_name.suffix, file_bytes)
    parsed = parse_file(file_kind, file_bytes, upload_name.suffix)
    return FileUploadPayload(
        title=upload_name.title, parsed=parsed, file_kind=file_kind,
        file_name=upload_name.basename, file_size=len(file_bytes),
    )


# 做什么：只读固定长度的文件头探测视频，再按路径解析
# 入参：规范化的文件名、临时文件路径和实际写入字节数
# 返回：视频文档载荷；探测或语音转写失败时抛业务错误
async def parse_video_upload(upload_name: UploadName, temporary_path: Path, file_size: int) -> FileUploadPayload:
    with temporary_path.open("rb") as uploaded_file:
        file_header = uploaded_file.read(TEXT_SAMPLE_BYTES)
    file_kind = require_supported_file(upload_name.suffix, file_header)
    parsed = await parse_video_file(str(temporary_path), upload_name.suffix)
    return FileUploadPayload(upload_name.title, parsed, file_kind, upload_name.basename, file_size)


# 做什么：根据文件后缀选用磁盘暂存参数
# 入参：规范化的小写后缀
# 返回：是否视频、最大字节数、临时文件名前缀及超限异常
def upload_file_limits(suffix: str) -> tuple[bool, int, str, KnowledgeUploadSizeError]:
    if FILE_EXTENSION_KINDS.get(suffix) == FileKind.VIDEO:
        return True, DOCUMENT_VIDEO_MAX_BYTES, VIDEO_TEMP_PREFIX, KnowledgeUploadSizeError(DOCUMENT_VIDEO_SIZE_ERROR)
    return False, DOCUMENT_FILE_MAX_BYTES, FILE_TEMP_PREFIX, KnowledgeUploadSizeError()


# 做什么：接收文件正文，遇到合法的结束边界就停止读请求
# 入参：写入器、文件头后已读的正文和后续请求块
# 返回：文件正文的实际字节数；超限或封装无效时拒绝
async def write_multipart_file(
    writer: MultipartFileWriter, first_chunk: bytes, chunks: AsyncIterator[bytes],
) -> int:
    await to_thread.run_sync(writer.feed, first_chunk)
    if writer.closed:
        return writer.finish()
    async for chunk in chunks:
        await to_thread.run_sync(writer.feed, chunk)
        if writer.closed:
            break
    return writer.finish()


# 做什么：把唯一的文件字段逐段写入临时文件，解析结束后删除原件
# 入参：分隔标记、文件名、文件头中剩余正文、后续请求块
# 返回：解析好的载荷；任何失败都先清理自己的临时文件
async def receive_and_parse(
    boundary: bytes, upload_name: UploadName, first_chunk: bytes, chunks: AsyncIterator[bytes],
) -> FileUploadPayload:
    is_video, byte_limit, prefix, size_error = upload_file_limits(upload_name.suffix)
    # 新手注意：临时文件像取号寄存柜，文件名由系统生成，解析完按自己的号码清理。
    temporary_file = NamedTemporaryFile(prefix=prefix, suffix=upload_name.suffix if is_video else "", delete=False)
    temporary_path = Path(temporary_file.name)
    try:
        with temporary_file:
            writer = MultipartFileWriter(temporary_file, boundary, byte_limit, size_error)
            file_size = await write_multipart_file(writer, first_chunk, chunks)
        if is_video:
            return await parse_video_upload(upload_name, temporary_path, file_size)
        # 字节通道超时后可以自行结束，此时工作线程已经不依赖磁盘文件。
        file_bytes = await to_thread.run_sync(temporary_path.read_bytes)
        return await to_thread.run_sync(parse_file_upload, upload_name, file_bytes, abandon_on_cancel=True)
    finally:
        temporary_path.unlink()


# 做什么：限量读取文件头，并按后缀调整当前上传的总时限
# 入参：Content-Type、请求流、取消范围和接收开始时刻
# 返回：解析好的载荷；格式、大小或时限无效时拒绝
async def receive_multipart(
    content_type: str, body: AsyncIterable[bytes], timeout: CancelScope, started_at: float,
) -> FileUploadPayload:
    boundary = multipart_boundary(content_type)
    chunks = iter_multipart_chunks(body)
    filename, first_chunk = await read_upload_header(chunks, boundary)
    upload_name = parse_upload_name(filename)
    if FILE_EXTENSION_KINDS.get(upload_name.suffix) == FileKind.VIDEO:
        timeout.deadline = started_at + DOCUMENT_VIDEO_PARSE_TIMEOUT_SECONDS
    return await receive_and_parse(boundary, upload_name, first_chunk, chunks)


# 做什么：给接收和解析设共同的时间上限，并屏蔽解析器内部细节
# 入参：请求类型和字节流，关联用户及知识库编号
# 返回：解析好的载荷；超时、封装错误和格式错误返回业务异常
async def prepare_file_upload(
    content_type: str, body: AsyncIterable[bytes], owner_id: int, knowledge_base_id: int,
) -> FileUploadPayload:
    try:
        started_at = current_time()
        with fail_after(DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS) as timeout:
            return await receive_multipart(content_type, body, timeout, started_at)
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
# 返回：文件文档的公开字段；解析失败时不创建文档
async def upload_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, content_type: str, body: AsyncIterable[bytes],
) -> KnowledgeDocumentItem:
    await to_thread.run_sync(check_upload_owner, db_session, owner_id, knowledge_base_id)
    payload = await prepare_file_upload(content_type, body, owner_id, knowledge_base_id)
    persist = partial(
        create_file_knowledge_document, db_session, owner_id, knowledge_base_id,
        title=payload.title, parsed=payload.parsed, file_kind=payload.file_kind,
        file_name=payload.file_name, file_size=payload.file_size,
    )
    return await to_thread.run_sync(persist)
