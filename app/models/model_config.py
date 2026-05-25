"""模型配置元数据 ORM 模型。"""

from sqlalchemy import String, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
from app.models.base import TimestampMixin


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)  # api | ollama | remote
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_params: Mapped[dict] = mapped_column(JSON, nullable=False, default={})
