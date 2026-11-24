"""知识库文档的固定状态与字段长度。"""
from enum import IntEnum

DOCUMENT_TITLE_LENGTH = 100
DOCUMENT_CONTENT_LENGTH = 50000


# 新手注意：状态枚举像带编号的标签，数据库和接口都用编号，代码里用名称。
class DocumentStatus(IntEnum):
    PENDING = 1  # 待切分
    PARSING = 2  # 解析中
    CHUNKING = 3  # 切分中
    INDEXING = 4  # 建索引中
    READY = 5  # 可检索
    FAILED = 6  # 处理失败
