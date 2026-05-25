"""MCP（Model Context Protocol）相关 ORM 模型。

阶段二要求：在 MySQL 中存储 MCP Server 配置、工具元数据及调用会话日志。

主要使用方：
- `app/services/mcp_runtime_service.py`：从数据库加载 Server
- `app/services/mcp_gateway_service.py`：列出并调用工具
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import TimestampMixin


class McpServerConfig(Base, TimestampMixin):
    """存储在 MySQL 中的 MCP Server 运行时配置。"""

    __tablename__ = "mcp_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # API 使用的逻辑 Server 标识，同一用户下唯一。
    server_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # stdio 启动信息。
    command: Mapped[str] = mapped_column(String(512), nullable=False)
    # 传给 MCP Server 子进程的命令行参数。
    args_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    env_json: Mapped[dict] = mapped_column(JSON, nullable=False, default={})
    working_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    startup_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    restart_policy: Mapped[str] = mapped_column(String(32), nullable=False, server_default="restart")


class McpToolMetadata(Base, TimestampMixin):
    """MCP 工具元数据快照，用于缓存与发现页展示。"""

    __tablename__ = "mcp_tools"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    server_config_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 以 JSON 保存原始 inputSchema，供前端后续渲染表单。
    input_schema_json: Mapped[dict] = mapped_column(JSON, nullable=False, default={})


class McpToolCallSession(Base, TimestampMixin):
    """绑定聊天会话的工具调用日志。

    便于调试与追溯，并满足阶段二对「会话」存储的要求。
    """

    __tablename__ = "mcp_tool_call_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    server_config_id: Mapped[int] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)

    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_json: Mapped[dict] = mapped_column(JSON, nullable=False, default={})
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="success")
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)

