"""文本与 PDF 通道：Markdown 按标题切，PDF 按页切。"""
import re
from io import BytesIO

from pypdf import PdfReader

from app.core.errors import KnowledgeUploadContentError, KnowledgeUploadParseError
from app.core.knowledge import DOCUMENT_PDF_MAX_PAGES
from app.rag.chunking import ChunkDraft
from app.services.knowledge_channel_common import make_draft
from app.services.knowledge_file_text import decode_plain_text

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
MARKDOWN_SUFFIX = ".md"
HEADING_JOINER = " > "
PDF_PAGE_HEADING = "第{}页"
NO_PDF_TEXT_MESSAGE = "这份 PDF 没有文字层（多半是扫描件），需要 OCR 能力后才能处理"


# 做什么：按文件后缀决定按标题切还是整篇切片
# 入参：文本文件的原始字节与后缀
# 返回：块列表；编码无法识别或正文为空时抛业务异常
def parse_plain(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    content = decode_plain_text(file_bytes)
    if not content.strip():
        raise KnowledgeUploadContentError()
    if suffix != MARKDOWN_SUFFIX:
        return make_draft("", content)
    return split_markdown(content)


# 做什么：沿 Markdown 标题层级累积正文，遇标题就收一块
# 入参：已解码的 Markdown 全文
# 返回：带"第2章 > 安装"这类路径的块列表
def split_markdown(content: str) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    for line in content.splitlines():
        match = MARKDOWN_HEADING.match(line)
        if match is None:
            buffer.append(line)
            continue
        if buffer:
            drafts.extend(make_draft(current_path(stack), "\n".join(buffer)))
            buffer = []
        level = len(match.group(1))
        stack = [item for item in stack if item[0] < level] + [(level, match.group(2).strip())]
        # 新手注意：标题行本身也留在正文里，否则原文缺标题、引用偏移对不上。
        buffer = [line]
    if buffer:
        drafts.extend(make_draft(current_path(stack), "\n".join(buffer)))
    return drafts


# 做什么：把标题栈拼成路径
# 入参：由浅到深的（层级，标题）列表
# 返回："第2章 > 安装"形式的路径；栈为空时返回空串
def current_path(stack: list[tuple[int, str]]) -> str:
    return HEADING_JOINER.join(title for _, title in stack)


# 做什么：逐页读取文字层，每页成一块
# 入参：PDF 原始字节与后缀（后缀不参与判断）
# 返回：带页码路径的块列表；加密、超页数或没有文字时抛业务异常
def parse_pdf(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    with PdfReader(BytesIO(file_bytes)) as reader:
        if reader.is_encrypted:
            raise KnowledgeUploadParseError("PDF 已加密，请解密后重新上传")
        if len(reader.pages) > DOCUMENT_PDF_MAX_PAGES:
            raise KnowledgeUploadContentError(f"PDF 最多支持 {DOCUMENT_PDF_MAX_PAGES} 页，请拆分后上传")
        for index, page in enumerate(reader.pages, start=1):
            drafts.extend(make_draft(PDF_PAGE_HEADING.format(index), page.extract_text() or ""))
    if not drafts:
        raise KnowledgeUploadParseError(NO_PDF_TEXT_MESSAGE)
    return drafts
