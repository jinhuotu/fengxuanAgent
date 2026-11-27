"""通过真实 multipart 路由验证文件解析和入库的事务边界。"""
from io import BytesIO
from time import sleep

import pytest
from docx import Document
from pypdf import PdfWriter
from sqlalchemy import func, select

from app.core.knowledge import DOCUMENT_CONTENT_LENGTH, DOCUMENT_FILE_MAX_BYTES, DocumentStatus
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services import knowledge_upload
from tests.test_knowledge import DOCUMENT_URL, OTHER_BASE_ID, document_bases, knowledge_client, knowledge_sessions, owner_headers

FILE_URL = f"{DOCUMENT_URL}/file"
DOCX_TEXT = "段落正文\n\n表格左栏 | 表格右栏\n\n结束段落"
PDF_TEXT = "Text layer from a PDF"


# 做什么：现场创建有正文、表格、页眉页脚和批注的 Word 夹具
# 入参：无，使用固定的短句
# 返回：可直接做 multipart 上传体的 DOCX 字节
def word_file() -> bytes:
    document = Document()
    paragraph = document.add_paragraph("段落正文")
    document.add_comment(paragraph.runs, text="不应见到的批注", author="测试")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "表格左栏"
    table.cell(0, 1).text = "表格右栏"
    document.add_paragraph("结束段落")
    document.sections[0].header.paragraphs[0].text = "不应见到的页眉"
    document.sections[0].footer.paragraphs[0].text = "不应见到的页脚"
    output = BytesIO()
    document.save(output)
    return output.getvalue()


# 做什么：手写一页 PDF，通过交叉引用表生成可读的最小文件
# 入参：页内绘制指令，可为文字或只有图片
# 返回：完整 PDF 字节
def minimal_pdf(page_commands: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(page_commands) + page_commands + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, contents in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(b"%d 0 obj\n" % number + contents + b"\nendobj\n")
    xref_start = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(b"%010d 00000 n \n" % offset)
    output.extend(b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n%d\n%%%%EOF\n" % xref_start)
    return bytes(output)


# 做什么：生成含固定 ASCII 文字层的 PDF 夹具
# 入参：无
# 返回：可检索正文与 PDF_TEXT 一致的 PDF
def text_pdf() -> bytes:
    commands = f"BT /F1 12 Tf 10 100 Td ({PDF_TEXT}) Tj ET"
    return minimal_pdf(commands.encode("ascii"))


# 做什么：确认拒绝的请求不会留下文档或切片
# 入参：测试数据库会话工厂
# 返回：无，任何残留都会断言失败
def assert_no_documents(knowledge_sessions) -> None:
    with knowledge_sessions() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0


# 做什么：确认每种格式抽取出的原文都入库，切片偏移与原文一致
# 入参：隔离客户端、文件字节构造函数、文件名、预期原文与块数
# 返回：无；标题、就绪状态、块数或切片文字偏离预期时断言失败
@pytest.mark.parametrize(("filename", "content_factory", "expected", "expected_chunks"), [
    ("中文.txt", lambda: "中文纯文本\r\n保留换行".encode("gb18030"), "中文纯文本\r\n保留换行", 1),
    ("知识笔记.MD", lambda: "# UTF-8 笔记\n段落内容".encode("utf-8"), "# UTF-8 笔记\n段落内容", 1),
    ("报告.docx", word_file, DOCX_TEXT, 3),
    ("文字层.pdf", text_pdf, PDF_TEXT, 1),
])
def test_upload_file_indexes_extracted_text(
    knowledge_client, knowledge_sessions, owner_headers, document_bases,
    filename, content_factory, expected, expected_chunks,
) -> None:
    uploaded = knowledge_client.post(FILE_URL, files={"file": (filename, content_factory())}, headers=owner_headers)
    assert uploaded.status_code == 200, uploaded.json()
    document = uploaded.json()["data"]
    assert document["title"] == filename.rsplit(".", 1)[0]
    assert document["char_count"] == len(expected)
    assert document["status"] == DocumentStatus.READY
    with knowledge_sessions() as session:
        assert session.get(KnowledgeDocument, document["id"]).raw_content == expected
    response = knowledge_client.get(f"{DOCUMENT_URL}/{document['id']}/chunks", headers=owner_headers)
    assert response.status_code == 200 and response.json()["data"]["total"] == expected_chunks
    items = response.json()["data"]["items"]
    for chunk in items:
        assert chunk["content"] == expected[chunk["char_start"]:chunk["char_end"]]
    assert "\n\n".join(chunk["content"] for chunk in items) == expected


# 做什么：确认格式、原件大小、正文、编码和非文字 PDF 错误都不入库
# 入参：隔离夹具及不同的失败样例
# 返回：无；状态码、业务码、文案或事务边界不符合预期时断言失败
@pytest.mark.parametrize(("filename", "payload", "status", "code", "message"), [
    ("资料.csv", b"table", 415, 4005,
     "暂不支持 .csv，当前支持 .docx / .md / .pdf / .pptx / .txt / .xlsx"),
    ("超大.txt", b"a" * (DOCUMENT_FILE_MAX_BYTES + 1), 413, 4006, "文件不能超过 10 MB，请拆分后上传"),
    ("正文超长.md", b"a" * (DOCUMENT_CONTENT_LENGTH + 1), 422, 4008, "文件正文不能超过 50000 字，请拆分后上传"),
    ("空白.txt", b" \n\t", 422, 4008, "文件没有可用正文，请检查内容后上传"),
    ("未知编码.txt", b"\xff", 415, 4005,
     "无法识别文件内容，可能已损坏；当前支持 .docx / .md / .pdf / .pptx / .txt / .xlsx"),
    ("损坏.docx", b"not a zip file", 415, 4005,
     "扩展名 .docx 与文件实际类型（文本）不符，请确认文件是否被改名"),
    ("扫描.pdf", minimal_pdf(b"q 20 0 0 20 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI Q"),
     422, 4007, "这份 PDF 没有文字层（多半是扫描件），需要 OCR 能力后才能处理"),
    ("长" * 101 + ".txt", b"valid text", 400, 4004, "文件名去掉扩展名后须为 1–100 字，请重命名后上传"),
])
def test_reject_file_without_leaving_document(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, filename, payload, status, code, message,
) -> None:
    response = knowledge_client.post(FILE_URL, files={"file": (filename, payload)}, headers=owner_headers)
    assert response.status_code == status
    assert response.json()["code"] == code and response.json()["message"] == message
    assert_no_documents(knowledge_sessions)


# 做什么：验证 200 页上限在尝试抽取空白 PDF 内容前生效
# 入参：隔离客户端、会话工厂和本人库
# 返回：无；超页数没有拒绝或写入文档时断言失败
def test_reject_pdf_over_page_limit(knowledge_client, knowledge_sessions, owner_headers, document_bases) -> None:
    writer = PdfWriter()
    for _page in range(201):
        writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    response = knowledge_client.post(FILE_URL, files={"file": ("过长.pdf", output.getvalue())}, headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["code"] == 4008 and response.json()["message"] == "PDF 最多支持 200 页，请拆分后上传"
    assert_no_documents(knowledge_sessions)


# 做什么：确认缺少指定的 file 字段时整单拒绝
# 入参：隔离客户端和本人库
# 返回：无；接受错误字段时断言失败
def test_reject_multipart_without_file(knowledge_client, knowledge_sessions, owner_headers, document_bases) -> None:
    response = knowledge_client.post(FILE_URL, files={"other": ("资料.txt", b"body")}, headers=owner_headers)
    assert response.status_code == 400 and response.json()["code"] == 4004
    assert_no_documents(knowledge_sessions)


# 做什么：用非法扩展名验证先判归属再处理文件
# 入参：本人凭证与他人的文档地址
# 返回：无；泄漏解析结果或越权写入时断言失败
def test_file_upload_checks_ownership_first(knowledge_client, knowledge_sessions, owner_headers, document_bases) -> None:
    url = f"/knowledge-bases/{OTHER_BASE_ID}/documents/file"
    response = knowledge_client.post(url, files={"file": ("unowned.zip", b"not allowed")}, headers=owner_headers)
    assert response.status_code == 404 and response.json()["code"] == 4001
    assert_no_documents(knowledge_sessions)


# 做什么：模拟超时后的解析器仍在运行，验证它无法继续写库
# 入参：隔离客户端、数据库与替换解析器的 monkeypatch
# 返回：无；未按时返回错误或留下任何记录时断言失败
def test_file_parsing_timeout_creates_no_rows(
    knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch,
) -> None:
    # 做什么：模拟一个耗时超过接口等待上限的同步解析器
    # 入参：已接收的请求类型和字节
    # 返回：延迟给出本来可入库的正文
    def slow_parse(content_type: str, payload: bytearray):
        sleep(0.1)
        return real_parse(content_type, payload)

    real_parse = knowledge_upload.parse_file_upload
    monkeypatch.setattr(knowledge_upload, "parse_file_upload", slow_parse)
    monkeypatch.setattr(knowledge_upload, "DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS", 0.02)
    response = knowledge_client.post(FILE_URL, files={"file": ("超时.txt", b"valid text")}, headers=owner_headers)
    assert response.status_code == 408
    assert response.json()["code"] == 4009
    assert_no_documents(knowledge_sessions)
