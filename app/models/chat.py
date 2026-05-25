"""聊天会话与消息 ORM 模型。"""

from sqlalchemy import String, ForeignKey, Text, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base
from app.models.base import TimestampMixin


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="新会话", nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    # 客户端可选标签（如设备或 UI 上下文）；可空以兼容旧数据。
    client_label: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 助手回复轮次：该条回复中按顺序使用的工具名（内置 + MCP）。
    tools_used_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
