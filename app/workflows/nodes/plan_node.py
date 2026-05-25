"""工作流 v1：工具规划节点。"""

from __future__ import annotations

from app.workflows.state import WorkflowState


def _tool_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    return type(tool).__name__


def run_plan_node(state: WorkflowState) -> WorkflowState:
    """生成轻量级工具调用计划，便于观测与调试。"""
    names = [_tool_name(t) for t in state.get("tools", [])]
    if state.get("need_tool"):
        state["tool_plan"] = names
    else:
        state["tool_plan"] = []
    state["node_events"].append(
        {
            "node": "plan",
            "planned_tools": state["tool_plan"],
        }
    )
    return state
