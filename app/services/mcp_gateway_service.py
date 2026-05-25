"""
MCP 网关服务（统一调用层）。

阶段二要求：
- list_tools(server_id)：从指定 MCP Server 获取工具元数据
- invoke_tool(server_id, tool_name, args)：经 stdio 网关执行 MCP 工具
- 屏蔽 MCP SDK 细节，返回标准化可 JSON 序列化结果
- 记录错误并控制超时
- 在 MySQL 中缓存工具元数据与调用会话
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.models.mcp import McpServerConfig, McpToolCallSession, McpToolMetadata
from app.services.mcp_runtime_service import get_mcp_runtime_service


class McpGatewayService:
    """基于 `McpRuntimeService` 的统一网关。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runtime = get_mcp_runtime_service()

    def _get_server_by_server_id(self, server_id: str, user_id: int) -> McpServerConfig:
        server = (
            self.db.query(McpServerConfig)
            .filter(
                McpServerConfig.server_id == server_id,
                McpServerConfig.user_id == user_id,
                McpServerConfig.enabled == 1,
            )
            .first()
        )
        if not server:
            raise ValueError(f"MCP server not found or disabled: {server_id}")
        return server

    async def list_tools(self, server_id: str, user_id: int) -> list[dict]:
        server = self._get_server_by_server_id(server_id, user_id)
        tools = await self.runtime.list_tools(server)
        self._cache_tools(server.id, tools)
        return tools

    def _cache_tools(self, server_config_id: int, tools: list[dict]) -> None:
        """
        将工具元数据快照缓存到 MySQL。

        阶段二采用简单策略：删除该 Server 已有行，再插入最新列表。
        """

        try:
            self.db.query(McpToolMetadata).filter(McpToolMetadata.server_config_id == server_config_id).delete()
            for t in tools:
                self.db.add(
                    McpToolMetadata(
                        server_config_id=server_config_id,
                        tool_name=t.get("name"),
                        description=t.get("description"),
                        input_schema_json=t.get("inputSchema") or {},
                    )
                )
            self.db.commit()
        except Exception as e:
            # 缓存失败不应阻塞工具调用主流程。
            self.db.rollback()
            logger.warning("MCP tool cache failed: %s", e)

    async def invoke_tool(
        self,
        server_id: str,
        tool_name: str,
        args: dict[str, Any],
        user_id: int,
        timeout_seconds: int = 60,
        chat_session_id: int | None = None,
    ) -> dict:
        server = self._get_server_by_server_id(server_id, user_id)
        result = await self.runtime.call_tool(server, tool_name=tool_name, args=args, timeout_seconds=timeout_seconds)
        self._log_tool_call(server.id, tool_name, args, result, chat_session_id=chat_session_id)
        return result

    def _log_tool_call(
        self,
        server_config_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        chat_session_id: int | None,
    ) -> None:
        if chat_session_id is None:
            return
        try:
            self.db.add(
                McpToolCallSession(
                    server_config_id=server_config_id,
                    chat_session_id=chat_session_id,
                    tool_name=tool_name,
                    arguments_json=args or {},
                    status="success" if (result and result.get("text") is not None) else "unknown",
                    result_text=result.get("text"),
                )
            )
            self.db.commit()
        except Exception as e:
            # 写入调用日志失败时回滚；不影响工具结果返回。
            self.db.rollback()
            logger.warning("MCP tool call log failed: %s", e)

    async def get_capabilities(self, user_id: int) -> dict:
        """
        返回已启用的 MCP Server 列表及其可用工具。

        返回结构（data）示例::
            {"servers": [{"server_id": "...", "name": "...", "tools": [...]}]}
        """

        servers = (
            self.db.query(McpServerConfig)
            .filter(McpServerConfig.user_id == user_id, McpServerConfig.enabled == 1)
            .all()
        )
        out_servers: list[dict] = []
        for s in servers:
            try:
                tools = await self.list_tools(s.server_id, user_id)
            except Exception as e:
                # 使用 %r：部分异常 str(e) 为空（例如重载期间的取消/超时），避免日志里出现 err= 无内容。
                logger.warning(
                    "MCP capabilities list_tools failed: server=%s err=%r type=%s",
                    s.server_id,
                    e,
                    type(e).__name__,
                )
                tools = []
            out_servers.append({"server_id": s.server_id, "name": s.name, "tools": tools})

        enabled = len(out_servers) > 0
        return {
            "enabled": enabled,
            "message": "MCP servers enabled" if enabled else "No MCP servers enabled",
            "servers": out_servers,
        }

