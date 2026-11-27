"""按文件字节判断真实类型；扩展名只用来和结果对照，不作为依据。"""
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from app.core.errors import KnowledgeUploadTypeError
from app.core.knowledge import ENABLED_FILE_KINDS, FILE_EXTENSION_KINDS, FILE_KIND_NAMES, FileKind

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
# 老版 Office（.doc/.ppt/.xls）是 OLE 复合文档，不是 zip，无法用现有解析器读
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
WEBM_MAGIC = b"\x1aE\xdf\xa3"
FTYP_TAIL = b"ftyp"
MAGIC_HEAD_BYTES = 8
FTYP_START = 4
FTYP_END = 8
TEXT_SAMPLE_BYTES = 4096
TEXT_ENCODINGS = ("utf-8", "gb18030")
# 新手注意：制表、换行、回车是正常文本排版，不算控制字节；其余 0-8、11-12、14-31、127 才算。
CONTROL_BYTES = frozenset({*range(0, 9), 11, 12, *range(14, 32), 127})
DOCX_ENTRY = "word/document.xml"
PPTX_ENTRY = "ppt/presentation.xml"
XLSX_ENTRY = "xl/workbook.xml"
MACRO_SUFFIX = "vbaProject.bin"
OFFICE_CONTAINER_KINDS = frozenset({FileKind.DOCX, FileKind.PPTX, FileKind.XLSX})


# 做什么：从文件头几个字节识别真实类型
# 入参：文件原始字节
# 返回：可识别的类型；识别不了或容器损坏时返回空
def sniff_file_kind(file_bytes: bytes) -> FileKind | None:
    head = file_bytes[:MAGIC_HEAD_BYTES]
    if head.startswith(PDF_MAGIC):
        return FileKind.PDF
    if head.startswith(PNG_MAGIC) or head.startswith(JPEG_MAGIC):
        return FileKind.IMAGE
    if head.startswith(WEBM_MAGIC) or head[FTYP_START:FTYP_END] == FTYP_TAIL:
        return FileKind.VIDEO
    if head.startswith(ZIP_MAGIC):
        return zip_container_kind(file_bytes)
    # 新手注意：纯文本没有"文件头"，只能靠能不能正常解码、有没有大量控制字节来判断。
    if looks_like_text(file_bytes):
        return FileKind.TEXT
    return None


# 做什么：把 office 压缩包按内部目录区分成 Word、PPT、Excel
# 入参：文件原始字节（已确认是 zip）
# 返回：对应的类型；不是这几种文档或压缩包损坏时返回空
def zip_container_kind(file_bytes: bytes) -> FileKind | None:
    names = zip_entry_names(file_bytes)
    if not names:
        return None
    if DOCX_ENTRY in names:
        return FileKind.DOCX
    if PPTX_ENTRY in names:
        return FileKind.PPTX
    if XLSX_ENTRY in names:
        return FileKind.XLSX
    return None


# 做什么：读出压缩包里的文件清单
# 入参：文件原始字节
# 返回：条目名列表；压缩包损坏时返回空列表，由调用方按"无法识别"处理
def zip_entry_names(file_bytes: bytes) -> list[str]:
    try:
        with ZipFile(BytesIO(file_bytes)) as archive:
            return archive.namelist()
    except (BadZipFile, OSError):
        return []


# 做什么：判断文件是否为老版 Office 的 OLE 复合文档
# 入参：文件原始字节
# 返回：是 .doc/.ppt/.xls 这类老格式时为真
def is_legacy_office(file_bytes: bytes) -> bool:
    return file_bytes[:MAGIC_HEAD_BYTES].startswith(OLE_MAGIC)


# 做什么：判断 Office 文档里是否带宏
# 入参：文件原始字节
# 返回：存在 vbaProject.bin 时为真；宏可能被执行，一律拒绝
def contains_office_macro(file_bytes: bytes) -> bool:
    return any(name.endswith(MACRO_SUFFIX) for name in zip_entry_names(file_bytes))


# 做什么：判断字节像不像纯文本
# 入参：文件原始字节
# 返回：能用 UTF-8 或 GB18030 解码且控制字节很少时为真
def looks_like_text(file_bytes: bytes) -> bool:
    sample = file_bytes[:TEXT_SAMPLE_BYTES]
    if not sample:
        return False
    decoded = None
    for encoding in TEXT_ENCODINGS:
        try:
            decoded = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        return False
    control_count = sum(1 for byte in sample if byte in CONTROL_BYTES)
    return control_count * 100 <= len(sample)


# 做什么：把支持的后缀拼成一句提示
# 入参：无，读取已启用的类型表
# 返回：形如 ".txt / .md / .pdf" 的文案
def supported_suffix_text() -> str:
    enabled_suffixes = sorted(
        suffix for suffix, kind in FILE_EXTENSION_KINDS.items() if kind in ENABLED_FILE_KINDS
    )
    return " / ".join(enabled_suffixes)


# 做什么：校验真实类型并确认该通道已启用
# 入参：文件后缀和原始字节
# 返回：通过校验的真实类型；各类不符抛 400/4005 并带具体原因
def require_supported_file(suffix: str, file_bytes: bytes) -> FileKind:
    if is_legacy_office(file_bytes):
        raise KnowledgeUploadTypeError("这是旧版 Office 文件（.doc/.ppt/.xls），请另存为 .docx/.pptx/.xlsx 后上传")
    kind = sniff_file_kind(file_bytes)
    if kind is None:
        raise KnowledgeUploadTypeError(f"无法识别文件内容，可能已损坏；当前支持 {supported_suffix_text()}")
    if kind not in ENABLED_FILE_KINDS:
        raise KnowledgeUploadTypeError(f"{FILE_KIND_NAMES[kind]}通道暂未启用，当前支持 {supported_suffix_text()}")
    expected_kind = FILE_EXTENSION_KINDS.get(suffix)
    if expected_kind is None:
        raise KnowledgeUploadTypeError(f"暂不支持 {suffix}，当前支持 {supported_suffix_text()}")
    if expected_kind != kind:
        raise KnowledgeUploadTypeError(
            f"扩展名 {suffix} 与文件实际类型（{FILE_KIND_NAMES[kind]}）不符，请确认文件是否被改名"
        )
    if kind in OFFICE_CONTAINER_KINDS and contains_office_macro(file_bytes):
        raise KnowledgeUploadTypeError("文件包含宏（vbaProject），出于安全考虑不接收，请另存为不含宏的版本")
    return kind
