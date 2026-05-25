"""工作流 v1：工具执行节点。"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.messages import BaseMessage, HumanMessage

from app.workflows.state import WorkflowState


def run_execute_tools_node(state: WorkflowState) -> WorkflowState:
    """运行支持工具调用的 Agent，并保存原始消息列表。"""
    plan_text = ", ".join(state.get("tool_plan", [])) or "无需预设工具"
    system_prompt = (
        "你是锋煊Agent助手。你可以通过工具获取信息并据此回答：\n"
        "1) 当需要查找用户知识库内容时，请调用 `retrieve_context`。\n"
        "2) 当需要检查后端运行状态时，请调用 `get_system_status`。\n"
        "3) 当用户询问当前时间、日期、星期几等需要实时时钟的信息时，请调用 `get_current_time`（可传 IANA 时区如 Asia/Shanghai）。\n"
        "4) 当用户询问 Python 版本、解释器、运行环境相关信息时，请调用 `get_python_runtime`。\n"
        "5) 当用户明确要求回显、测试 echo 或验证工具调用时，请调用 `get_echo`。\n"
        "6) 你会收到一个工具规划提示，仅用于参考，不是强制执行。\n"
        "回答请尽量用简短列表呈现要点。\n"
        "请优先使用工具返回的真实结果，而不是凭空编造。\n"
        "最终必须给出清晰可读的中文答案。"
    )

    agent = create_agent(
        model=state["llm"],
        tools=state["tools"],
        system_prompt=system_prompt,
    )

    messages: list[BaseMessage] = list(state["chat_history"]) + [
        HumanMessage(content=f"{state['prompt']}\n\n[工具规划参考] {plan_text}")
    ]
    result = agent.invoke({"messages": messages})
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    state["agent_messages"] = msgs
    state["node_events"].append(
        {
            "node": "execute_tools",
            "message_count": len(msgs),
        }
    )
    return state
