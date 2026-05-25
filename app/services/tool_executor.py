"""统一工具执行服务（M2）。

为外部工具提供超时、重试及可审计的执行策略。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.services.mcp_gateway_service import McpGatewayService


class ToolExecutor:
    """在共享可靠性策略下执行工具。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.gateway = McpGatewayService(db)

    async def execute_mcp_tool(
        self,
        *,
        user_id: int,
        chat_session_id: int,
        server_id: str,
        tool_name: str,
        args: dict[str, Any],
        timeout_seconds: int = 20,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """执行单个 MCP 工具，含超时/重试与统一结果结构。"""
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = await self.gateway.invoke_tool(
                    server_id=server_id,
                    tool_name=tool_name,
                    args=args,
                    user_id=user_id,
                    timeout_seconds=timeout_seconds,
                    chat_session_id=chat_session_id,
                )
                return {
                    "ok": True,
                    "result": result,
                    "attempt": attempt + 1,
                }
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(
                    "ToolExecutor MCP call failed: server=%s tool=%s attempt=%s err=%s",
                    server_id,
                    tool_name,
                    attempt + 1,
                    e,
                )
        return {
            "ok": False,
            "error": str(last_error) if last_error else "unknown error",
            "attempt": max_retries + 1,
        }

    def execute_mcp_tool_sync(
        self,
        *,
        user_id: int,
        chat_session_id: int,
        server_id: str,
        tool_name: str,
        args: dict[str, Any],
        timeout_seconds: int = 20,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """同步封装，供 FastAPI 同步端点或 Agent 同步 invoke 使用。"""
        coro = self.execute_mcp_tool(
            user_id=user_id,
            chat_session_id=chat_session_id,
            server_id=server_id,
            tool_name=tool_name,
            args=args,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        # 若已在运行中的事件循环内调用，则在独立循环中执行，以保持 LangChain 同步 `agent.invoke` 接口稳定。
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()
