"""聊天工作流运行器（M1）。"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.workflows.nodes.answer_node import run_answer_node
from app.workflows.nodes.execute_tools_node import run_execute_tools_node
from app.workflows.nodes.intent_node import run_intent_node
from app.workflows.nodes.plan_node import run_plan_node
from app.workflows.state import WorkflowState

logger = logging.getLogger("agent-backend")


def _build_chat_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("intent", run_intent_node)
    graph.add_node("plan", run_plan_node)
    graph.add_node("execute_tools", run_execute_tools_node)
    graph.add_node("answer", run_answer_node)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "plan")
    graph.add_edge("plan", "execute_tools")
    graph.add_edge("execute_tools", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


CHAT_WORKFLOW = _build_chat_workflow()


def run_chat_workflow(state: WorkflowState) -> WorkflowState:
    """执行工作流并输出简洁的节点级追踪日志。"""
    out = CHAT_WORKFLOW.invoke(state)
    events = out.get("node_events", []) if isinstance(out, dict) else []
    logger.info(
        "WorkflowTrace: session_id=%s model_id=%s nodes=%s",
        out.get("session_id") if isinstance(out, dict) else None,
        out.get("model_id") if isinstance(out, dict) else None,
        events,
    )
    return out
