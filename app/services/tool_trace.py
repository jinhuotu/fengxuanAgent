"""从 LangGraph / LangChain Agent 消息列表中提取面向用户的工具名。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def _tool_call_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name") or "").strip()
    return str(getattr(tc, "name", "") or "").strip()


def collect_tool_names_from_agent_messages(messages: list[Any]) -> list[str]:
    """
    返回本轮 Agent 实际使用过的工具名（有序且去重）。

    优先使用 `ToolMessage.name`（已执行）；必要时用 `AIMessage.tool_calls` 补充
    （例如 ToolMessage 上缺少 name 字段时）。
    """
    seen: set[str] = set()
    ordered: list[str] = []

    for msg in messages:
        if isinstance(msg, ToolMessage):
            nm = (getattr(msg, "name", None) or "").strip()
            if nm and nm not in seen:
                seen.add(nm)
                ordered.append(nm)
        elif isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", None) or []:
                nm = _tool_call_name(tc)
                if nm and nm not in seen:
                    seen.add(nm)
                    ordered.append(nm)

    return ordered
