"""在内存中提取纯文本，不保留上传原件。"""
from collections.abc import Iterable, Iterator
from io import BytesIO
from itertools import chain

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.core.errors import KnowledgeUploadContentError, KnowledgeUploadParseError
from app.core.knowledge import DOCUMENT_CONTENT_LENGTH, DOCUMENT_PDF_MAX_PAGES

TEXT_ENCODINGS = ("utf-8-sig", "gb18030")
NO_PDF_TEXT_MESSAGE = "未识别到文字，暂不支持扫描件"


# 做什么：优先按 UTF-8 解码，失败后再尝试兼容中文文本的 GB18030
# 入参：TXT 或 Markdown 的原始字节
# 返回：保留换行的文本；两种编码都失败时抛业务异常
def decode_plain_text(content: bytes) -> str:
    for encoding in TEXT_ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise KnowledgeUploadParseError("文件编码无法识别")


# 做什么：按原顺序展开 Word 表格里的单元格
# 入参：正文中的表格
# 返回：单元格里的段落与内嵌表格；合并单元格只读一次
def word_table_blocks(table: Table) -> Iterator[Paragraph | Table]:
    seen_cells = set()
    cells = chain.from_iterable(row.cells for row in table.rows)
    for cell in cells:
        # 新手注意：合并格像跨两行的座位，Word 会在两行里指向同一个格子。
        if cell._tc not in seen_cells:
            seen_cells.add(cell._tc)
            yield from cell.iter_inner_content()


# 做什么：沿 Word 正文顺序取段落文本和表格文本
# 入参：正文容器给出的段落、表格
# 返回：逐段输出文本；页眉、页脚和批注不在这些块中
def word_lines(blocks: Iterable[Paragraph | Table]) -> Iterator[str]:
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block.text
        else:
            yield from word_lines(word_table_blocks(block))


# 做什么：只逐页读取 PDF 文字层
# 入参：PDF 文件的原始字节
# 返回：可提取文字的页内容；加密或超过页数限制时抛业务异常
def pdf_text_pages(content: bytes) -> Iterator[str]:
    with PdfReader(BytesIO(content)) as reader:
        if reader.is_encrypted:
            raise KnowledgeUploadParseError("PDF 已加密，请解密后重新上传")
        if len(reader.pages) > DOCUMENT_PDF_MAX_PAGES:
            raise KnowledgeUploadContentError(f"PDF 最多支持 {DOCUMENT_PDF_MAX_PAGES} 页，请拆分后上传")
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text.strip():
                yield page_text.rstrip("\n")


# 做什么：按换行拼接段落或页面，在累计超限时停止解析
# 入参：逐段或逐页给出文本的迭代器
# 返回：完整纯文本；超出正文上限时抛异常，不返回截断结果
def join_text_parts(parts: Iterable[str]) -> str:
    collected = []
    char_count = 0
    for part in parts:
        char_count += len(part) + bool(collected)
        if char_count > DOCUMENT_CONTENT_LENGTH:
            raise KnowledgeUploadContentError(f"文件正文不能超过 {DOCUMENT_CONTENT_LENGTH} 字，请拆分后上传")
        collected.append(part)
    return "\n".join(collected)


# 做什么：按后缀选择解析器，并检查最终正文能否入库
# 入参：已校验的扩展名和文件字节
# 返回：完整可入库正文；无文字和超限时给出对应业务错误
def extract_file_text(extension: str, file_bytes: bytes) -> str:
    if extension == ".docx":
        document = Document(BytesIO(file_bytes))
        content = join_text_parts(word_lines(document.iter_inner_content()))
    elif extension == ".pdf":
        content = join_text_parts(pdf_text_pages(file_bytes))
        if not content:
            raise KnowledgeUploadParseError(NO_PDF_TEXT_MESSAGE)
    else:
        content = decode_plain_text(file_bytes)
    if not content.strip():
        raise KnowledgeUploadContentError()
    if len(content) > DOCUMENT_CONTENT_LENGTH:
        raise KnowledgeUploadContentError(f"文件正文不能超过 {DOCUMENT_CONTENT_LENGTH} 字，请拆分后上传")
    return content
