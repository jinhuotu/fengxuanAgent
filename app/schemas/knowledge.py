"""个人知识库的请求、公开字段、分页和检索结果。"""
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.knowledge import (
    DOCUMENT_CONTENT_LENGTH,
    DOCUMENT_TITLE_LENGTH,
    SEARCH_QUERY_MAX_CHARS,
    SEARCH_QUERY_MIN_CHARS,
    SEARCH_TOP_K_DEFAULT,
    SEARCH_TOP_K_MAX,
    DocumentSource,
    DocumentStatus,
    FileKind,
)
from app.schemas.user import DEFAULT_PAGE_NUMBER, DEFAULT_PAGE_SIZE

MAX_KNOWLEDGE_BASE_NAME_LENGTH = 50
MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH = 200
MAX_KNOWLEDGE_BASE_PAGE_SIZE = 50


class KnowledgeBaseCreate(BaseModel):
    # 新手注意：先剪掉库名两头的空白，再量剩下的字符数。
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_KNOWLEDGE_BASE_NAME_LENGTH)
    ] = Field(description="库名")
    description: str | None = Field(
        default=None, max_length=MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH, description="说明，可不填"
    )


class KnowledgeBaseListQuery(BaseModel):
    page: int = Field(default=DEFAULT_PAGE_NUMBER, ge=1, description="页码，从一开始")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_KNOWLEDGE_BASE_PAGE_SIZE, description="每页条数"
    )


class KnowledgeBaseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    document_count: int = 0
    created_at: datetime


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseItem]
    total: int
    page: int
    page_size: int


class KnowledgeDocumentCreate(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DOCUMENT_TITLE_LENGTH)
    ] = Field(description="文档标题")
    content: str = Field(min_length=1, max_length=DOCUMENT_CONTENT_LENGTH, description="纯文本原文")


class KnowledgeDocumentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    char_count: int
    status: DocumentStatus
    source_type: DocumentSource
    file_type: FileKind
    file_name: str | None
    file_size: int | None
    created_at: datetime


class KnowledgeFileTypeItem(BaseModel):
    """可上传的文件类型；enabled 为假表示识别得出但解析通道还没打通。"""

    kind: int
    name: str
    extensions: list[str]
    enabled: bool
    note: str | None = None


class KnowledgeDocumentListResponse(BaseModel):
    items: list[KnowledgeDocumentItem]
    total: int
    page: int
    page_size: int


class KnowledgeChunkItem(BaseModel):
    id: int
    chunk_index: int
    heading_path: str
    char_start: int
    char_end: int
    content: str


class KnowledgeChunkListResponse(BaseModel):
    items: list[KnowledgeChunkItem]
    total: int
    page: int
    page_size: int


class KnowledgeChunkDetail(BaseModel):
    chunk_id: int
    knowledge_base_id: int
    document_id: int
    document_title: str
    chunk_index: int
    heading_path: str
    char_start: int
    char_end: int
    content: str


class KnowledgeBaseDeleteResult(BaseModel):
    id: int
    deleted_documents: int
    deleted_chunks: int
    cleared_sessions: int


class KnowledgeDocumentDeleteResult(BaseModel):
    id: int
    deleted_chunks: int


class KnowledgeChunkSearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=SEARCH_QUERY_MIN_CHARS, max_length=SEARCH_QUERY_MAX_CHARS
    )]
    top_k: int = Field(default=SEARCH_TOP_K_DEFAULT, ge=1, le=SEARCH_TOP_K_MAX)


class KnowledgeChunkSearchItem(BaseModel):
    chunk_id: int
    document_id: int
    heading_path: str
    content: str
    char_start: int
    char_end: int
    score: float = Field(description="MySQL 全文检索的相关性得分")


class KnowledgeChunkSearchResponse(BaseModel):
    items: list[KnowledgeChunkSearchItem]
    candidates: int = Field(ge=0, description="筛选前实际取到的候选条数")
    dropped: int = Field(ge=0, description="候选条数减去最终返回的命中条数")
