"""内置 LangChain 工具的用户偏好（非 MCP）。"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class UserBuiltinToolPref(Base, TimestampMixin):
    """按用户启用/禁用内置工具（如 get_system_status）。"""

    __tablename__ = "user_builtin_tool_prefs"
    __table_args__ = (UniqueConstraint("user_id", "tool_key", name="uq_user_builtin_tool"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
