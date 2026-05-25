"""聊天执行流程的工作流状态定义。"""

from typing import Any, NotRequired, TypedDict

from langchain_core.messages import BaseMessage


class WorkflowState(TypedDict):
    """各工作流节点共享的状态字典。"""

    user_message: str
    context: str
    prompt: str
    answer: str
    chat_history: list[BaseMessage]
    llm: Any
    tools: list[Any]
    need_tool: bool
    tool_plan: list[str]
    agent_messages: list[Any]
    tools_used: list[str]  # execute_tools 节点实际调用的工具名（供前端展示）
    node_events: list[dict[str, Any]]
    session_id: NotRequired[int]
    model_id: NotRequired[int]
