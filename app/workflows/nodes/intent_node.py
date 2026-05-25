"""工作流 v1：意图识别节点。"""

from __future__ import annotations

from app.workflows.state import WorkflowState


def run_intent_node(state: WorkflowState) -> WorkflowState:
    """判断本轮对话是否可能需要调用工具。"""
    prompt = state.get("prompt", "")
    lowered = prompt.lower()
    hints = (
        "知识库",
        "文档",
        "检索",
        "查一下",
        "状态",
        "system",
        "数据库",
        "mcp",
        "工具",
        "python",
        "解释器",
        "运行时",
        "八字",
        "排盘",
        "命理",
        "合婚",
        "生肖",
        "罗喉",
        "杀师",
    )
    state["need_tool"] = any(k in prompt or k in lowered for k in hints)
    state["node_events"].append(
        {
            "node": "intent",
            "need_tool": state["need_tool"],
        }
    )
    return state
