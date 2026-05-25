"""为 LangChain 工具调用动态构建的 MCP 工具。"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool
from pydantic import Field, create_model
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.models.mcp import McpServerConfig, McpToolMetadata
from app.services.tool_executor import ToolExecutor


def _resolve_json_type(schema: dict[str, Any]) -> str:
    t = schema.get("type")
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        for item in t:
            if item != "null":
                return str(item)
    return "object"


def _python_type_from_json_schema(schema: dict[str, Any]) -> type:
    t = _resolve_json_type(schema)
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        item_type = _python_type_from_json_schema(item_schema) if item_schema else Any
        return list[item_type]  # type: ignore[valid-type]
    if t == "object":
        return dict[str, Any]
    return Any


def _build_args_model(lc_tool_name: str, schema: dict[str, Any]):
    """在可能时根据 MCP inputSchema 为每个工具生成参数模型。"""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None

    required = schema.get("required")
    required_set = set(required) if isinstance(required, list) else set()
    field_defs: dict[str, tuple[type, Any]] = {}
    for raw_name, prop in properties.items():
        if not isinstance(raw_name, str) or not raw_name:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name)
        if not safe_name:
            continue
        prop_schema = prop if isinstance(prop, dict) else {}
        py_type = _python_type_from_json_schema(prop_schema)
        desc = prop_schema.get("description")
        if not isinstance(desc, str) or not desc.strip():
            desc = f"MCP 参数 `{raw_name}`"
        if raw_name in required_set:
            field_defs[safe_name] = (py_type, Field(..., description=desc))
        else:
            field_defs[safe_name] = (py_type | None, Field(default=None, description=desc))

    if not field_defs:
        return None
    return create_model(f"Args_{lc_tool_name}", **field_defs)  # type: ignore[misc]


def _safe_tool_name(server_id: str, tool_name: str) -> str:
    raw = f"mcp__{server_id}__{tool_name}".lower()
    return re.sub(r"[^a-z0-9_]", "_", raw)


def _load_mcp_tools_from_db(db: Session, user_id: int) -> list[dict[str, Any]]:
    """
    从 MySQL 缓存读取 MCP 工具元数据（与 GET /mcp/capabilities 写入的 mcp_tools 表一致）。

    不在此处 asyncio.run 去连 stdio：chat 接口在 threadpool 中执行，临时事件循环会与
    McpRuntimeService 的全局 stdio 连接生命周期冲突（anyio cancel scope / GeneratorExit）。
    用户需先在界面「刷新工具列表」或调用 capabilities，以写入缓存。
    """
    rows = (
        db.query(McpServerConfig)
        .filter(McpServerConfig.user_id == user_id, McpServerConfig.enabled.is_(True))
        .order_by(McpServerConfig.id.asc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for s in rows:
        tool_rows = (
            db.query(McpToolMetadata)
            .filter(McpToolMetadata.server_config_id == s.id)
            .order_by(McpToolMetadata.id.asc())
            .all()
        )
        tools: list[dict[str, Any]] = []
        for tr in tool_rows:
            tools.append(
                {
                    "name": tr.tool_name,
                    "description": tr.description,
                    "inputSchema": tr.input_schema_json if isinstance(tr.input_schema_json, dict) else {},
                }
            )
        out.append({"server_id": s.server_id, "name": s.name, "tools": tools})
        if not tools:
            logger.info(
                "MCP server has no cached tools (refresh capabilities in UI): server_id=%s user_id=%s",
                s.server_id,
                user_id,
            )
    return out


def build_mcp_dynamic_tools(db: Session, user_id: int, chat_session_id: int) -> list[Any]:
    """为每条 MCP 工具元数据构建一个可调用工具。"""
    servers = _load_mcp_tools_from_db(db, user_id)
    if not servers:
        return []

    executor = ToolExecutor(db)
    out: list[Any] = []
    for s in servers:
        server_id = str(s.get("server_id") or "").strip()
        if not server_id:
            continue
        tools = s.get("tools", [])
        if not isinstance(tools, list):
            continue
        for t in tools:
            mcp_tool_name = str(t.get("name") or "").strip()
            if not mcp_tool_name:
                continue
            lc_tool_name = _safe_tool_name(server_id, mcp_tool_name)
            desc = str(t.get("description") or "").strip()
            schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
            typed_model = _build_args_model(lc_tool_name, schema)
            if typed_model is None:
                schema_hint = schema if schema else {"type": "object"}
                typed_model = create_model(  # type: ignore[misc]
                    f"Args_{lc_tool_name}",
                    arguments=(
                        dict[str, Any],
                        Field(default_factory=dict, description=f"MCP 工具入参(JSON对象): {schema_hint}"),
                    ),
                )
                fb_desc = (
                    f"MCP 工具（server={server_id}, tool={mcp_tool_name}）。"
                    f"{desc or '无描述'}。参数请通过 arguments 传 JSON 对象。"
                )

                @tool(lc_tool_name, args_schema=typed_model, description=fb_desc)
                def _invoke_fallback(
                    arguments: dict[str, Any], _sid: str = server_id, _tn: str = mcp_tool_name
                ) -> str:
                    result = executor.execute_mcp_tool_sync(
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        server_id=_sid,
                        tool_name=_tn,
                        args=arguments or {},
                        timeout_seconds=20,
                        max_retries=1,
                    )
                    if result.get("ok"):
                        payload = result.get("result", {})
                        text = payload.get("text") if isinstance(payload, dict) else ""
                        return str(text or "")
                    return f"[MCP_TOOL_ERROR] server={_sid} tool={_tn} error={result.get('error')}"

                out.append(_invoke_fallback)
                continue

            typed_desc = (
                f"MCP 工具（server={server_id}, tool={mcp_tool_name}）。"
                f"{desc or '无描述'}。参数请按字段直接传递，无需包装 arguments。"
            )

            @tool(lc_tool_name, args_schema=typed_model, description=typed_desc)
            def _invoke_typed(_sid: str = server_id, _tn: str = mcp_tool_name, **kwargs: Any) -> str:
                args = {k: v for k, v in kwargs.items() if v is not None}
                result = executor.execute_mcp_tool_sync(
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    server_id=_sid,
                    tool_name=_tn,
                    args=args,
                    timeout_seconds=20,
                    max_retries=1,
                )
                if result.get("ok"):
                    payload = result.get("result", {})
                    text = payload.get("text") if isinstance(payload, dict) else ""
                    return str(text or "")
                return f"[MCP_TOOL_ERROR] server={_sid} tool={_tn} error={result.get('error')}"

            out.append(_invoke_typed)
    logger.info("MCP dynamic tools loaded: user_id=%s count=%s", user_id, len(out))
    return out
