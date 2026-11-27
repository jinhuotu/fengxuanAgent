"""Office 通道：Word 按章节、PPT 按幻灯片、Excel 按工作表与行块切。"""
from io import BytesIO

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pptx import Presentation

from app.core.errors import KnowledgeUploadContentError
from app.core.knowledge import (
    DOCUMENT_PPT_MAX_SLIDES,
    DOCUMENT_SHEET_ROWS_PER_CHUNK,
    DOCUMENT_XLSX_MAX_ROWS,
    TABLE_CELL_SEPARATOR,
)
from app.rag.chunking import ChunkDraft
from app.services.knowledge_channel_common import make_draft

HEADING_STYLE_PREFIX = "Heading"
TABLE_HEADING_SUFFIX = "·表格"
SLIDE_HEADING = "第{}张幻灯片"
SHEET_HEADING = "{}!第{}-{}行"
NOTES_PREFIX = "备注"
COLUMN_FALLBACK = "第{}列"
CELL_JOINER = "；"
HEADER_JOINER = " | "


# 做什么：把 Word 正文按标题样式分章节，表格单独成块
# 入参：docx 原始字节与后缀（后缀不参与判断）
# 返回：块列表；表格会转成"列名：值"的文本，不丢列名
def parse_docx(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    document = Document(BytesIO(file_bytes))
    drafts: list[ChunkDraft] = []
    heading = ""
    buffer: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if is_heading_paragraph(block) and block.text.strip():
                if buffer:
                    drafts.extend(make_draft(heading, "\n".join(buffer)))
                    buffer = []
                heading = block.text.strip()
            buffer.append(block.text)
            continue
        if buffer:
            drafts.extend(make_draft(heading, "\n".join(buffer)))
            buffer = []
        drafts.extend(make_draft(f"{heading}{TABLE_HEADING_SUFFIX}", table_to_text(block)))
    if buffer:
        drafts.extend(make_draft(heading, "\n".join(buffer)))
    return drafts


# 做什么：判断段落是否为标题样式
# 入参：Word 段落
# 返回：样式名以 Heading 开头时为真（中文版 Word 的标题样式名也是 Heading N）
def is_heading_paragraph(paragraph: Paragraph) -> bool:
    style = paragraph.style
    return style is not None and bool(style.name) and style.name.startswith(HEADING_STYLE_PREFIX)


# 做什么：把表格拍成"列名：值"的文本行
# 入参：Word 表格
# 返回：首行为表头、其后每行一条记录；空表返回空串
def table_to_text(table: Table) -> str:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    headers = [text or COLUMN_FALLBACK.format(index + 1) for index, text in enumerate(rows[0])]
    lines = [HEADER_JOINER.join(headers)]
    for row in rows[1:]:
        pairs = [
            f"{headers[index]}{TABLE_CELL_SEPARATOR}{value}"
            for index, value in enumerate(row)
            if index < len(headers) and value
        ]
        if pairs:
            lines.append(CELL_JOINER.join(pairs))
    return "\n".join(lines)


# 做什么：每张幻灯片成一块，正文按形状顺序拼接，备注附在后面
# 入参：pptx 原始字节与后缀（后缀不参与判断）
# 返回：带幻灯片序号的块列表；超过页数上限时抛业务异常
def parse_pptx(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    presentation = Presentation(BytesIO(file_bytes))
    slides = presentation.slides
    if len(slides) > DOCUMENT_PPT_MAX_SLIDES:
        raise KnowledgeUploadContentError(f"PPT 最多支持 {DOCUMENT_PPT_MAX_SLIDES} 页，请拆分后上传")
    drafts: list[ChunkDraft] = []
    for index, slide in enumerate(slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            parts.extend(shape_texts(shape))
        notes = slide_notes(slide)
        if notes:
            parts.append(f"{NOTES_PREFIX}{TABLE_CELL_SEPARATOR}{notes}")
        drafts.extend(make_draft(SLIDE_HEADING.format(index), "\n".join(parts)))
    return drafts


# 做什么：取一个形状里的文字和表格
# 入参：幻灯片上的形状
# 返回：可拼接的文本片段列表；图片等没有文字的形状返回空
def shape_texts(shape) -> list[str]:
    texts: list[str] = []
    if shape.has_text_frame and shape.text_frame.text.strip():
        texts.append(shape.text_frame.text)
    if getattr(shape, "has_table", False):
        texts.append(table_to_text(shape.table))
    return texts


# 做什么：取幻灯片备注页里的文字
# 入参：一张幻灯片
# 返回：备注正文；没有备注页或备注为空时返回空串
def slide_notes(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text.strip() if frame is not None else ""


# 做什么：每个工作表按行块切，块内重复表头
# 入参：xlsx 原始字节与后缀（后缀不参与判断）
# 返回：带"工作表!第N-M行"路径的块列表；行数超限时抛业务异常
def parse_xlsx(file_bytes: bytes, suffix: str) -> list[ChunkDraft]:
    workbook = load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    try:
        drafts: list[ChunkDraft] = []
        for sheet in workbook.worksheets:
            rows = [[cell_text(value) for value in row] for row in sheet.iter_rows(values_only=True)]
            drafts.extend(sheet_drafts(sheet.title, rows))
        return drafts
    finally:
        workbook.close()


# 做什么：把一个工作表的行切成若干块
# 入参：工作表名与全部行（含表头）
# 返回：块列表；每块首行是重复的表头
def sheet_drafts(title: str, rows: list[list[str]]) -> list[ChunkDraft]:
    if len(rows) > DOCUMENT_XLSX_MAX_ROWS:
        raise KnowledgeUploadContentError(
            f"工作表 {title} 超过 {DOCUMENT_XLSX_MAX_ROWS} 行，请拆分后上传"
        )
    if not rows:
        return []
    headers = [text or COLUMN_FALLBACK.format(index + 1) for index, text in enumerate(rows[0])]
    drafts: list[ChunkDraft] = []
    for start in range(1, len(rows), DOCUMENT_SHEET_ROWS_PER_CHUNK):
        block = rows[start:start + DOCUMENT_SHEET_ROWS_PER_CHUNK]
        lines = [f"{HEADER_JOINER.join(headers)}"]
        lines.extend(row_line(headers, row) for row in block)
        heading = SHEET_HEADING.format(title, start + 1, start + len(block))
        drafts.extend(make_draft(heading, "\n".join(line for line in lines if line)))
    return drafts


# 做什么：把一行单元格拼成"列名：值"
# 入参：表头与一行数据
# 返回：非空单元格组成的一行；整行都空时返回空串
def row_line(headers: list[str], row: list[str]) -> str:
    pairs = [
        f"{headers[index]}{TABLE_CELL_SEPARATOR}{value}"
        for index, value in enumerate(row)
        if index < len(headers) and value
    ]
    return CELL_JOINER.join(pairs)


# 做什么：把单元格值转成文本
# 入参：openpyxl 读出的值，可能是数字、日期或空
# 返回：去掉多余空白的字符串；空单元格返回空串
def cell_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
