"""按探测到的真实类型把文件分发到对应通道；通道只负责解析与分块。"""
from app.core.errors import KnowledgeUploadContentError
from app.core.knowledge import DOCUMENT_CONTENT_LENGTH, FILE_KIND_NAMES, FileKind
from app.services.knowledge_channel_common import ParsedFile, finalize_drafts
from app.services.knowledge_channel_office import parse_docx, parse_pptx, parse_xlsx
from app.services.knowledge_channel_text import parse_pdf, parse_plain

# 新手注意：这张表像总机号码表，按真实类型转接，通道之间互不调用。
CHANNELS = {
    FileKind.TEXT: parse_plain,
    FileKind.PDF: parse_pdf,
    FileKind.DOCX: parse_docx,
    FileKind.PPTX: parse_pptx,
    FileKind.XLSX: parse_xlsx,
}


# 做什么：按类型选用通道并把块拼成可入库的原文
# 入参：真实类型、文件字节、文件后缀
# 返回：解析结果；通道未启用、正文为空或超长时抛业务异常
def parse_file(kind: int, file_bytes: bytes, suffix: str) -> ParsedFile:
    channel = CHANNELS.get(kind)
    if channel is None:
        raise KnowledgeUploadContentError(f"{FILE_KIND_NAMES.get(kind, '该')}通道暂未启用")
    parsed = finalize_drafts(channel(file_bytes, suffix))
    if not parsed.text.strip():
        raise KnowledgeUploadContentError("文件没有可用正文，请检查内容后上传")
    if len(parsed.text) > DOCUMENT_CONTENT_LENGTH:
        raise KnowledgeUploadContentError(f"文件正文不能超过 {DOCUMENT_CONTENT_LENGTH} 字，请拆分后上传")
    return parsed
