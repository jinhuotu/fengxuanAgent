"""知识库元数据 ORM 模型。"""

from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
from app.models.base import TimestampMixin


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chroma_collection: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class KnowledgeDocument(Base, TimestampMixin):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
