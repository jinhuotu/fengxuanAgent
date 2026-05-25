"""工作流 v1：答案汇总节点。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from app.services.tool_trace import collect_tool_names_from_agent_messages
from app.workflows.state import WorkflowState


def _last_assistant_text(messages: list[Any]) -> str:
    """从 Agent 消息列表中提取最后一条助手文本。"""
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if content is None:
            continue
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif hasattr(block, "text"):
                    parts.append(str(getattr(block, "text", "")))
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                return joined
    return ""


def run_answer_node(state: WorkflowState) -> WorkflowState:
    """根据 Agent 输出构建面向用户的最终答案。"""
    msgs = state.get("agent_messages", [])
    state["answer"] = _last_assistant_text(msgs)
    state["tools_used"] = collect_tool_names_from_agent_messages(msgs)
    state["node_events"].append(
        {
            "node": "answer",
            "answer_length": len(state["answer"]),
            "tools_used_count": len(state["tools_used"]),
        }
    )
    return state
