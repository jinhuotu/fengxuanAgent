"""个人知识库、文档与切片表结构。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.knowledge import (
    CHUNK_HEADING_PATH_LENGTH,
    DOCUMENT_FILE_NAME_LENGTH,
    DOCUMENT_TITLE_LENGTH,
    DocumentSource,
    FileKind,
)
from app.db.base import Base

# 新手注意：类型变体像两套编号模板，MySQL 用 BIGINT，SQLite 测试用 INTEGER 自增。
IDENTIFIER_TYPE = BigInteger().with_variant(Integer, "sqlite")
DELETION_TYPE = TINYINT().with_variant(Integer, "sqlite")
MAX_KNOWLEDGE_BASE_NAME_LENGTH = 50
MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH = 200
IS_NOT_DELETED = 0


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index("idx_knowledge_bases_owner_id", "owner_id", "is_deleted", "id"),
        {"comment": "知识库：用户自建的资料集合，后续挂文档与切片"},
    )

    id: Mapped[int] = mapped_column(
        IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="知识库编号，自增"
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, comment="归属用户编号，不建物理外键")
    name: Mapped[str] = mapped_column(String(MAX_KNOWLEDGE_BASE_NAME_LENGTH), comment="知识库名称")
    description: Mapped[str | None] = mapped_column(
        String(MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH), comment="知识库说明，可为空"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，东八区"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(
        DELETION_TYPE, default=IS_NOT_DELETED, server_default=str(IS_NOT_DELETED),
        comment="软删除标记，零表示正常，一表示删除",
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("idx_knowledge_documents_base_id", "knowledge_base_id", "is_deleted", "id"),
        {"comment": "知识库文档：保存原文与处理状态，后续切分与索引"},
    )

    id: Mapped[int] = mapped_column(
        IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="文档编号，自增"
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        BigInteger, comment="所属知识库编号，不建物理外键"
    )
    owner_id: Mapped[int] = mapped_column(BigInteger, comment="归属用户编号，用于过滤")
    title: Mapped[str] = mapped_column(String(DOCUMENT_TITLE_LENGTH), comment="文档标题")
    raw_content: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT, "mysql"), comment="未经处理的纯文本原文"
    )
    char_count: Mapped[int] = mapped_column(Integer, comment="原文字符数，包含空白")
    status: Mapped[int] = mapped_column(
        TINYINT().with_variant(Integer, "sqlite"),
        comment="处理状态：一待切分，二解析中，三切分中，四建索引中，五可检索，六处理失败",
    )
    source_type: Mapped[int] = mapped_column(
        TINYINT().with_variant(Integer, "sqlite"), default=DocumentSource.PASTE,
        server_default=str(DocumentSource.PASTE), comment="来源：一上传文件，二粘贴文本，三URL抓取",
    )
    file_type: Mapped[int] = mapped_column(
        TINYINT().with_variant(Integer, "sqlite"), default=FileKind.TEXT,
        server_default=str(FileKind.TEXT),
        comment="文件类型：一文本，二PDF，三Word，四PPT，五Excel，六图片，七视频",
    )
    file_name: Mapped[str | None] = mapped_column(
        String(DOCUMENT_FILE_NAME_LENGTH), comment="原始文件名，粘贴文本为空"
    )
    file_size: Mapped[int | None] = mapped_column(Integer, comment="原始文件字节数，粘贴文本为空")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，东八区"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(
        DELETION_TYPE, default=IS_NOT_DELETED, server_default=str(IS_NOT_DELETED),
        comment="软删除标记，零表示正常，一表示删除",
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("idx_knowledge_chunks_doc", "document_id", "is_deleted", "chunk_index"),
        Index("idx_knowledge_chunks_base", "knowledge_base_id", "is_deleted", "id"),
        {"comment": "知识库切片：保存文档片段与在原文中的字符位置"},
    )

    id: Mapped[int] = mapped_column(
        IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="切片编号，自增"
    )
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, comment="所属知识库编号")
    document_id: Mapped[int] = mapped_column(BigInteger, comment="所属文档编号")
    owner_id: Mapped[int] = mapped_column(BigInteger, comment="归属用户编号，用于过滤")
    chunk_index: Mapped[int] = mapped_column(Integer, comment="文档内切片序号，从零开始")
    heading_path: Mapped[str] = mapped_column(String(CHUNK_HEADING_PATH_LENGTH), comment="标题与段落路径")
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT, "mysql"), comment="切片正文")
    char_start: Mapped[int] = mapped_column(Integer, comment="原文起始字符偏移，包含该位置")
    char_end: Mapped[int] = mapped_column(Integer, comment="原文结束字符偏移，不包含该位置")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，东八区"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区"
    )
    is_deleted: Mapped[int] = mapped_column(
        DELETION_TYPE, default=IS_NOT_DELETED, server_default=str(IS_NOT_DELETED),
        comment="软删除标记，零表示正常，一表示删除",
    )
