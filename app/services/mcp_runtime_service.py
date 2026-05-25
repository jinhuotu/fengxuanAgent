"""
MCP 运行时服务（stdio + 本地子进程管理）。

阶段二要求：
- 从 MySQL 配置加载 MCP Server 信息。
- 使用 `StdioServerParameters` 创建 stdio 连接参数。
- 启动 / 监控 / 重启 MCP 子进程并管理 stdio 管道。
- 维护 MCP 客户端连接，支持多 Server 并行。
- 通信仅限 stdio（不使用 HTTP / WebSocket）。
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Any

from app.core.logger import logger
from app.models.mcp import McpServerConfig


@dataclass
class _McpHandle:
    """单个 MCP Server 的活跃客户端连接句柄。"""

    server_config_id: int
    stdio_cm: Any
    session_cm: Any
    session: Any
    lock: asyncio.Lock
    last_error: str | None = None
    restart_count: int = 0
    last_used_ts: float = 0.0


class McpRuntimeService:
    """管理 MCP Server 子进程生命周期与客户端会话。"""

    def __init__(self) -> None:
        self._handles: dict[int, _McpHandle] = {}
        self._global_lock = asyncio.Lock()

    async def _create_client_from_config(self, config: McpServerConfig) -> _McpHandle:
        """
        通过 stdio 启动 MCP Server 子进程，并创建已初始化的 ClientSession。
        """

        try:
            # 仅在运行时方法内导入，以便开发环境未安装 `mcp` 时后端仍可编译。
            # 使用 client 子模块，避免依赖 `mcp` 包 `__init__` 的再导出面（类型相同，意图更清晰）。
            from mcp.client.session import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except (ModuleNotFoundError, ImportError) as e:
            # 常见情况：终端里 pip 装在了 A 环境，而 uvicorn 用 B 环境启动，导致此处仍失败。
            raise RuntimeError(
                "未安装或无法加载 MCP Python SDK（`mcp` 包或其依赖）。"
                f"请在与当前后端相同的解释器中安装，例如: pip install \"mcp>=1.8.0,<2.0.0\"。"
                f" 当前 sys.executable={sys.executable!r}。原始错误: {e!r}。"
            ) from e

        server_params = StdioServerParameters(
            command=config.command,
            args=config.args_json or [],
            env=config.env_json or {},
        )
        if config.working_dir:
            # 部分 SDK 版本支持在参数上设置工作目录；尽力设置以保持兼容。
            try:
                server_params.working_dir = config.working_dir  # type: ignore[attr-defined]
            except Exception:
                pass

        # 保持 stdio_client 与 ClientSession 上下文管理器存活。
        stdio_cm = stdio_client(server_params)
        read, write = await stdio_cm.__aenter__()
        session_cm = ClientSession(read, write)
        session = await session_cm.__aenter__()
        await session.initialize()

        return _McpHandle(
            server_config_id=config.id,
            stdio_cm=stdio_cm,
            session_cm=session_cm,
            session=session,
            lock=asyncio.Lock(),
            last_used_ts=time.time(),
        )

    async def _close_handle(self, handle: _McpHandle) -> None:
        """优雅关闭会话与 stdio 上下文。"""

        exc_type = None
        exc = None
        tb = None

        try:
            # 先退出 ClientSession。
            if handle.session_cm is not None:
                await handle.session_cm.__aexit__(exc_type, exc, tb)
        except Exception as e:
            logger.warning("MCP session close failed: %s", e)

        try:
            # 再退出 stdio 客户端以终止子进程。
            if handle.stdio_cm is not None:
                await handle.stdio_cm.__aexit__(exc_type, exc, tb)
        except Exception as e:
            logger.warning("MCP stdio close failed: %s", e)

    async def get_session(self, config: McpServerConfig) -> Any:
        """确保给定 Server 配置下存在已初始化的 MCP 客户端会话。"""

        if not config.enabled:
            raise RuntimeError(f"MCP server is disabled: {config.server_id}")

        # 快路径：复用已有句柄。
        handle = self._handles.get(config.id)
        if handle is not None:
            handle.last_used_ts = time.time()
            return handle.session

        async with self._global_lock:
            # 在锁内再次检查，避免并发重复创建。
            handle = self._handles.get(config.id)
            if handle is not None:
                handle.last_used_ts = time.time()
                return handle.session

            startup_timeout = max(int(getattr(config, "startup_timeout_seconds", 30) or 30), 5)
            handle = await asyncio.wait_for(self._create_client_from_config(config), timeout=startup_timeout)
            self._handles[config.id] = handle
            logger.info("MCP server started: %s (%s)", config.server_id, config.name)
            return handle.session

    async def restart_session(self, config: McpServerConfig, reason: str) -> Any:
        """出错后重启 MCP 会话。"""

        handle = self._handles.get(config.id)
        if handle is None:
            # 无现有句柄可重启，直接新建连接。
            logger.warning("MCP handle not found; creating new. reason=%s", reason)
            return await self.get_session(config)

        async with handle.lock:
            # 等待 handle.lock 期间，其他协程可能已完成重启。
            handle = self._handles.get(config.id)
            if handle is None:
                return await self.get_session(config)

            try:
                handle.last_error = reason
                await self._close_handle(handle)
            finally:
                self._handles.pop(config.id, None)

            # 创建全新的客户端会话。
            new_handle = await self._create_client_from_config(config)
            new_handle.restart_count = (handle.restart_count + 1) if handle else 1
            self._handles[config.id] = new_handle
            logger.info("MCP server restarted: %s reason=%s", config.server_id, reason)
            return new_handle.session

    async def drop_handle(self, server_config_id: int) -> None:
        """关闭并移除指定 db id 的 MCP 连接（配置删除或更新后调用）。"""
        async with self._global_lock:
            handle = self._handles.pop(server_config_id, None)
        if not handle:
            return
        try:
            await self._close_handle(handle)
        except Exception as e:
            logger.warning("MCP drop_handle failed: id=%s err=%s", server_config_id, e)

    async def list_tools(self, config: McpServerConfig) -> list[dict]:
        """从已连接的 MCP Server 列出工具元数据。"""

        async def _list(session: Any) -> list[dict]:
            tools = await session.list_tools()
            # 将 SDK 工具对象转为可 JSON 序列化的简单字典。
            out: list[dict] = []
            for t in tools.tools:
                out.append(
                    {
                        "name": t.name,
                        "description": getattr(t, "description", None),
                        "inputSchema": getattr(t, "inputSchema", None),
                    }
                )
            return out

        startup_timeout = max(int(getattr(config, "startup_timeout_seconds", 30) or 30), 5)
        session = await asyncio.wait_for(self.get_session(config), timeout=startup_timeout)
        try:
            return await asyncio.wait_for(_list(session), timeout=startup_timeout)
        except Exception as e:
            logger.warning("MCP list_tools failed; will restart. server=%s err=%s", config.server_id, e)
            session = await self.restart_session(config, reason=f"list_tools error: {e}")
            return await asyncio.wait_for(_list(session), timeout=startup_timeout)

    async def call_tool(
        self, config: McpServerConfig, tool_name: str, args: dict[str, Any], timeout_seconds: int = 60
    ) -> dict:
        """调用 MCP 工具并返回标准化的可 JSON 序列化结果。"""

        async def _call(session: Any) -> dict:
            result = await session.call_tool(tool_name, arguments=args)
            # 将 MCP 调用结果转为纯文本；`result.content` 通常为 Content 块列表。
            text_parts: list[str] = []
            content = getattr(result, "content", None)
            if content:
                for block in content:
                    if hasattr(block, "text"):
                        text_parts.append(str(block.text))
                    elif hasattr(block, "content") and hasattr(block.content, "text"):
                        text_parts.append(str(block.content.text))
                    else:
                        text_parts.append(str(block))
            text = "\n".join([p for p in text_parts if p is not None]).strip()

            return {
                "tool_name": tool_name,
                "text": text,
            }

        session = await self.get_session(config)
        try:
            return await asyncio.wait_for(_call(session), timeout=timeout_seconds)
        except Exception as e:
            logger.warning("MCP call_tool failed; will restart. server=%s tool=%s err=%s", config.server_id, tool_name, e)
            session = await self.restart_session(config, reason=f"call_tool error: {e}")
            return await asyncio.wait_for(_call(session), timeout=timeout_seconds)

    async def shutdown(self) -> None:
        """优雅停止所有受管理的 MCP 客户端会话。"""

        # 复制句柄列表，避免迭代时修改字典。
        handles = list(self._handles.values())
        self._handles.clear()
        for handle in handles:
            try:
                await self._close_handle(handle)
            except Exception:
                # 吞掉异常，尽力完成关闭。
                pass


_runtime_singleton: McpRuntimeService | None = None


def get_mcp_runtime_service() -> McpRuntimeService:
    global _runtime_singleton
    if _runtime_singleton is None:
        _runtime_singleton = McpRuntimeService()
    return _runtime_singleton

