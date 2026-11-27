"""文件类型探测、通道分流、类型清单接口与 Office 通道的集成验证。"""
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from app.core.errors import KnowledgeUploadTypeError
from app.core.knowledge import DocumentSource, DocumentStatus, FileKind
from app.services.knowledge_channel_registry import parse_file
from app.services.knowledge_probe import (
    is_legacy_office,
    looks_like_text,
    require_supported_file,
    sniff_file_kind,
)
from tests.test_knowledge import DOCUMENT_URL, document_bases, knowledge_client, knowledge_sessions, owner_headers

FILE_URL = f"{DOCUMENT_URL}/file"
FILE_TYPES_URL = "/knowledge-bases/file-types"
SLIDE_ONE_TEXT = "封面标题"
NOTES_TEXT = "讲者备注"
SHEET_NAME = "成绩"
SHEET_HEADER = "姓名 | 分数"


# 做什么：现场生成带标题文字的两页 PPT
# 入参：无
# 返回：可上传的 pptx 字节，第二页带备注
def pptx_file() -> bytes:
    presentation = Presentation()
    for title in (SLIDE_ONE_TEXT, "第二页标题"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
    presentation.slides[0].notes_slide.notes_text_frame.text = NOTES_TEXT
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


# 做什么：现场生成 25 行成绩表，超过单块 20 行上限
# 入参：无
# 返回：可上传的 xlsx 字节
def xlsx_file() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(["姓名", "分数"])
    for index in range(24):
        sheet.append([f"学生{index + 1}", index + 60])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


# 做什么：给 docx 追加一个宏条目，模拟启用宏的文档
# 入参：原始 docx 字节
# 返回：带 word/vbaProject.bin 的新 docx 字节
def add_macro_entry(file_bytes: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(file_bytes)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("word/vbaProject.bin", b"fake-macro")
    return output.getvalue()


# 做什么：生成一份含标题样式章节与表格的 Word
# 入参：无
# 返回：可上传的 docx 字节
def docx_with_heading() -> bytes:
    document = Document()
    document.add_heading("安装步骤", level=1)
    document.add_paragraph("先装依赖")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


# 做什么：确认魔数识别的类型与后缀无关
# 入参：各类文件头与期望类型
# 返回：无；识别结果不符时断言失败
@pytest.mark.parametrize(("payload", "expected"), [
    (b"%PDF-1.4 trailer", FileKind.PDF),
    (b"\x89PNG\r\n\x1a\nbody", FileKind.IMAGE),
    (b"\xff\xd8\xff\xe0body", FileKind.IMAGE),
    (b"\x1aE\xdf\xa3body", FileKind.VIDEO),
    ("纯文本\r\n第二行".encode("utf-8"), FileKind.TEXT),
    (b"\x00\x01\x02\x03\x04\x05\x06\x07", None),
])
def test_sniff_kind_by_magic(payload: bytes, expected) -> None:
    assert sniff_file_kind(payload) == expected


# 做什么：确认换行不被当成控制字节，纯文本判定才可靠
# 入参：无
# 返回：无；含换行或 GB18030 中文的文本被判为非文本时断言失败
def test_looks_like_text_accepts_newlines() -> None:
    assert looks_like_text("第一行\r\n第二行\n".encode("utf-8")) is True
    assert looks_like_text("中文纯文本".encode("gb18030")) is True
    assert looks_like_text(b"\xff\x00\x01") is False


# 做什么：确认老版 Office 与宏文档都被拒绝，改后缀也绕不过
# 入参：无
# 返回：无；错误文案缺少原因时断言失败
def test_require_supported_file_blocks_unsafe_cases() -> None:
    legacy = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"
    assert is_legacy_office(legacy) is True
    with pytest.raises(KnowledgeUploadTypeError, match="旧版 Office"):
        require_supported_file(".docx", legacy)
    with pytest.raises(KnowledgeUploadTypeError, match="不符"):
        require_supported_file(".pdf", docx_with_heading())
    with pytest.raises(KnowledgeUploadTypeError, match="宏"):
        require_supported_file(".docx", add_macro_entry(docx_with_heading()))


# 做什么：确认 PPT 一页一块、备注跟随该页
# 入参：无
# 返回：无；块数、标题路径或备注缺失时断言失败
def test_pptx_channel_splits_by_slide() -> None:
    parsed = parse_file(FileKind.PPTX, pptx_file(), ".pptx")
    assert [draft.heading_path for draft in parsed.drafts] == ["第1张幻灯片", "第2张幻灯片"]
    assert SLIDE_ONE_TEXT in parsed.drafts[0].content and f"备注：{NOTES_TEXT}" in parsed.drafts[0].content


# 做什么：确认 Excel 每 20 行一块且后续块重复表头
# 入参：无
# 返回：无；块数或表头缺失时断言失败
def test_xlsx_channel_repeats_header() -> None:
    parsed = parse_file(FileKind.XLSX, xlsx_file(), ".xlsx")
    assert len(parsed.drafts) == 2
    assert parsed.drafts[0].heading_path.startswith(f"{SHEET_NAME}!第2-21行")
    assert parsed.drafts[1].content.splitlines()[0] == SHEET_HEADER


# 做什么：确认 Word 标题样式分章节，表格不丢列名
# 入参：无
# 返回：无；章节路径或表格行缺失时断言失败
def test_docx_channel_keeps_heading_and_table() -> None:
    document = Document()
    document.add_heading("安装步骤", level=1)
    document.add_paragraph("先装依赖")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "名称", "版本"
    table.cell(1, 0).text, table.cell(1, 1).text = "uv", "0.11"
    output = BytesIO()
    document.save(output)
    parsed = parse_file(FileKind.DOCX, output.getvalue(), ".docx")
    headings = [draft.heading_path for draft in parsed.drafts]
    assert headings[0] == "安装步骤" and headings[1] == "安装步骤·表格"
    assert "名称：uv；版本：0.11" in parsed.drafts[1].content


# 做什么：确认类型清单与后端真实能力一致
# 入参：隔离客户端与本人请求头
# 返回：无；可解析类型被标灰或后缀缺失时断言失败
def test_file_type_list_matches_capability(knowledge_client, owner_headers) -> None:
    response = knowledge_client.get(FILE_TYPES_URL, headers=owner_headers)
    assert response.status_code == 200
    items = {item["kind"]: item for item in response.json()["data"]}
    for kind in (FileKind.TEXT, FileKind.PDF, FileKind.DOCX, FileKind.PPTX, FileKind.XLSX):
        assert items[int(kind)]["enabled"] is True and items[int(kind)]["extensions"]
    image_item = items[int(FileKind.IMAGE)]
    assert image_item["enabled"] is False and image_item["note"]
    assert ".docx" in items[int(FileKind.DOCX)]["extensions"]


# 做什么：确认 PPT 上传后写回真实类型、文件名与大小
# 入参：隔离客户端、会话、请求头与预建库
# 返回：无；元数据或块数不符时断言失败
def test_upload_pptx_records_file_meta(knowledge_client, knowledge_sessions, owner_headers, document_bases) -> None:
    payload = pptx_file()
    uploaded = knowledge_client.post(FILE_URL, files={"file": ("季度汇报.pptx", payload)}, headers=owner_headers)
    assert uploaded.status_code == 200, uploaded.json()
    document = uploaded.json()["data"]
    assert (document["source_type"], document["file_type"]) == (DocumentSource.FILE, FileKind.PPTX)
    assert (document["file_name"], document["file_size"]) == ("季度汇报.pptx", len(payload))
    assert document["status"] == DocumentStatus.READY
    response = knowledge_client.get(f"{DOCUMENT_URL}/{document['id']}/chunks", headers=owner_headers)
    assert response.status_code == 200 and response.json()["data"]["total"] == 2
