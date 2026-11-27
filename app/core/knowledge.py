"""知识库文档状态、字段长度与切分检索参数。"""
from enum import IntEnum

DOCUMENT_TITLE_LENGTH = 100
DOCUMENT_CONTENT_LENGTH = 50000
DOCUMENT_FILE_MAX_BYTES = 10 * 1024 * 1024
DOCUMENT_MULTIPART_OVERHEAD_BYTES = 64 * 1024
DOCUMENT_FILE_PARSE_TIMEOUT_SECONDS = 30
DOCUMENT_PDF_MAX_PAGES = 200
DOCUMENT_FILE_EXTENSIONS = frozenset({
    ".txt", ".md", ".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".mp4", ".webm",
})
CHUNK_HEADING_PATH_LENGTH = 120
CHUNK_MIN_CHARS = 400
CHUNK_TARGET_MAX = 600
CHUNK_HARD_MAX = 800
CHUNK_OVERLAP_MIN = 50
CHUNK_OVERLAP_MAX = 80
SEARCH_QUERY_MIN_CHARS = 2
SEARCH_QUERY_MAX_CHARS = 200
SEARCH_TOP_K_DEFAULT = 5
SEARCH_TOP_K_MAX = 20
SEARCH_CANDIDATE_MIN = 10
RAG_TOP_K = 5
INDEX_PROBE_MAX_CHARS = 12
INDEX_PROBE_TOP_K = 1
INDEX_PROBE_RETRY_SECONDS = 0.2
SOFT_DELETED = 1


# 新手注意：状态枚举像带编号的标签，数据库和接口都用编号，代码里用名称。
class DocumentStatus(IntEnum):
    PENDING = 1  # 待切分
    PARSING = 2  # 解析中
    CHUNKING = 3  # 切分中
    INDEXING = 4  # 建索引中
    READY = 5  # 可检索
    FAILED = 6  # 处理失败


# 新手注意：文件类型枚举像检票口，探测出真实类型后只放行到对应通道。
class FileKind(IntEnum):
    """文件真实类型，编号会写进数据库，已有值不要重排。"""

    TEXT = 1
    PDF = 2
    DOCX = 3
    PPTX = 4
    XLSX = 5
    IMAGE = 6
    VIDEO = 7


class DocumentSource(IntEnum):
    """文档来源，决定页面"来源"列怎么显示。"""

    FILE = 1
    PASTE = 2
    URL = 3


FILE_KIND_NAMES = {
    FileKind.TEXT: "文本",
    FileKind.PDF: "PDF",
    FileKind.DOCX: "Word",
    FileKind.PPTX: "PPT",
    FileKind.XLSX: "Excel",
    FileKind.IMAGE: "图片",
    FileKind.VIDEO: "视频",
}
FILE_EXTENSION_KINDS = {
    ".txt": FileKind.TEXT,
    ".md": FileKind.TEXT,
    ".pdf": FileKind.PDF,
    ".docx": FileKind.DOCX,
    ".pptx": FileKind.PPTX,
    ".xlsx": FileKind.XLSX,
    ".png": FileKind.IMAGE,
    ".jpg": FileKind.IMAGE,
    ".jpeg": FileKind.IMAGE,
    ".mp4": FileKind.VIDEO,
    ".webm": FileKind.VIDEO,
}
# 真正打通解析的通道；图片与视频要等 OCR / ASR 引擎接入后才放进来
ENABLED_FILE_KINDS = frozenset({FileKind.TEXT, FileKind.PDF, FileKind.DOCX, FileKind.PPTX, FileKind.XLSX})
# 新手注意：识别得出但解析通道没打通的类型，前端会显示为灰态并带上这里的说明。
DISABLED_KIND_NOTES = {
    FileKind.IMAGE: "需要 OCR 引擎，暂未启用",
    FileKind.VIDEO: "需要语音转写引擎，暂未启用",
}
DOCUMENT_FILE_NAME_LENGTH = 200
DOCUMENT_PPT_MAX_SLIDES = 200
DOCUMENT_XLSX_MAX_ROWS = 20000
DOCUMENT_SHEET_ROWS_PER_CHUNK = 20
TABLE_CELL_SEPARATOR = "："
