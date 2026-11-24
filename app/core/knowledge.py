"""知识库文档状态、字段长度与切分检索参数。"""
from enum import IntEnum

DOCUMENT_TITLE_LENGTH = 100
DOCUMENT_CONTENT_LENGTH = 50000
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
RAG_TOP_K = 5


# 新手注意：状态枚举像带编号的标签，数据库和接口都用编号，代码里用名称。
class DocumentStatus(IntEnum):
    PENDING = 1  # 待切分
    PARSING = 2  # 解析中
    CHUNKING = 3  # 切分中
    INDEXING = 4  # 建索引中
    READY = 5  # 可检索
    FAILED = 6  # 处理失败
