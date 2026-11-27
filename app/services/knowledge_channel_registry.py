"""按探测到的真实类型把文件分发到对应通道；通道只负责解析与分块。"""
from app.core.errors import KnowledgeUploadContentError
from app.core.knowledge import DOCUMENT_CONTENT_LENGTH, FILE_KIND_NAMES, FileKind
from app.rag.chunking import ChunkDraft
from app.services.knowledge_channel_common import ParsedFile, finalize_drafts
from app.services.knowledge_channel_image import parse_image
from app.services.knowledge_channel_office import parse_docx, parse_pptx, parse_xlsx
from app.services.knowledge_channel_text import parse_pdf, parse_plain
from app.services.knowledge_channel_video import parse_video

# 新手注意：这张表像总机号码表，按真实类型转接，通道之间互不调用。
CHANNELS = {
    FileKind.TEXT: parse_plain,
    FileKind.PDF: parse_pdf,
    FileKind.DOCX: parse_docx,
    FileKind.PPTX: parse_pptx,
    FileKind.XLSX: parse_xlsx,
    FileKind.IMAGE: parse_image,
    FileKind.VIDEO: parse_video,
}


# 做什么：按类型选用通道并把块拼成可入库的原文
# 入参：真实类型、文件字节、文件后缀
# 返回：解析结果；通道未启用、正文为空或超长时抛业务异常
def parse_file(kind: FileKind, file_bytes: bytes, suffix: str) -> ParsedFile:
    if kind == FileKind.VIDEO:
        raise TypeError("视频通道需要临时文件路径")
    channel = CHANNELS.get(kind)
    if channel is None:
        raise KnowledgeUploadContentError(f"{FILE_KIND_NAMES.get(kind, '该')}通道暂未启用")
    return finish_file(channel(file_bytes, suffix))


# 做什么：用路径调用视频通道，走同一套原文和偏移计算
# 入参：视频临时路径和已经校验的后缀
# 返回：包含最终字符偏移的解析结果，失败时抛业务异常
async def parse_video_file(path: str, suffix: str) -> ParsedFile:
    drafts = await CHANNELS[FileKind.VIDEO](path, suffix)
    return finish_file(drafts)


# 做什么：拼接原文并检查通用的正文约束
# 入参：解析通道返回的切片
# 返回：原文及重算偏移的切片；正文为空或超长时抛业务错误
def finish_file(drafts: list[ChunkDraft]) -> ParsedFile:
    parsed = finalize_drafts(drafts)
    if not parsed.text.strip():
        raise KnowledgeUploadContentError("文件没有可用正文，请检查内容后上传")
    if len(parsed.text) > DOCUMENT_CONTENT_LENGTH:
        raise KnowledgeUploadContentError(f"文件正文不能超过 {DOCUMENT_CONTENT_LENGTH} 字，请拆分后上传")
    return parsed
