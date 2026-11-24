"""个人知识库表结构。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

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
