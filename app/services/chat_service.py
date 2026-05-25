"""基于工作流运行器的聊天服务（M1）。"""

import logging
from typing import Any, cast

logger = logging.getLogger("agent-backend")

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession
from app.models.knowledge_base import KnowledgeBase
from app.models.model_config import ModelConfig
from app.models.prompt import PromptTemplate
from app.services.model_service import get_model_adapter
from app.services.prompt_service import render_prompt
from app.services.agent_tool_service import get_effective_builtin_flags
from app.services.tools.builtin_registry import build_builtin_langchain_tools
from app.services.tools.mcp_tools import build_mcp_dynamic_tools
from app.workflows.runner import run_chat_workflow
from app.workflows.state import WorkflowState


def _missing_tools_used_column_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "tools_used_json" not in msg:
        return False
    return "unknown column" in msg or "doesn't exist" in msg or "does not exist" in msg or "no such column" in msg


def chat_once(
    db: Session,
    session: ChatSession,
    model_config: ModelConfig,
    user_message: str,
    template: PromptTemplate | None = None,
    kb: KnowledgeBase | None = None,
) -> tuple[str, int, str | None, list[str]]:
    msg_count = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).count()
    turn_count = (msg_count // 2) + 1
    logger.info(
        "chat_once: session_id=%s turn_count=%s user_id=%s",
        session.id,
        turn_count,
        session.user_id,
    )
    notice = "当前会话已超过 30 轮，为避免上下文过长建议开启新会话" if turn_count > 30 else None

    # 阶段一：不再预先对知识库做 RAG 拼接，让 LLM 通过工具按需检索。
    rendered_prompt = render_prompt(template, user_message, context="")

    # 从数据库读取最近 30 轮（<=60条消息）的对话历史，喂给 AgentExecutor。
    max_turns = 30
    history_limit_messages = max_turns * 2
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    recent_rows = rows[-history_limit_messages:]

    chat_history: list[BaseMessage] = []
    for m in recent_rows:
        if m.role == "user":
            chat_history.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            chat_history.append(AIMessage(content=m.content))

    adapter = get_model_adapter(model_config)
    # 工具列表：内置（按用户偏好）+ MCP 动态工具。
    builtin_flags = get_effective_builtin_flags(db, session.user_id)
    tools: list[Any] = build_builtin_langchain_tools(db, kb, builtin_flags)
    tools.extend(build_mcp_dynamic_tools(db, user_id=session.user_id, chat_session_id=session.id))

    state = {
        "user_message": rendered_prompt,
        "context": "",
        "prompt": rendered_prompt,
        "answer": "",
        "chat_history": chat_history,
        # 预先绑定工具，确保不同模型实现都能正确进入 structured tool calling 流程。
        "llm": adapter.bind_tools(tools),
        "tools": tools,
        "need_tool": False,
        "tool_plan": [],
        "agent_messages": [],
        "tools_used": [],
        "node_events": [],
        "session_id": session.id,
        "model_id": model_config.id,
    }
    out = run_chat_workflow(cast(WorkflowState, state))
    answer = out.get("answer", "") if isinstance(out, dict) else ""
    tools_used = out.get("tools_used", []) if isinstance(out, dict) else []
    if not isinstance(tools_used, list):
        tools_used = []

    db.add(ChatMessage(session_id=session.id, role="user", content=user_message, turn_index=turn_count))
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=answer,
            turn_index=turn_count,
            tools_used_json=tools_used if tools_used else None,
        )
    )
    try:
        db.commit()
    except (OperationalError, ProgrammingError) as e:
        db.rollback()
        if _missing_tools_used_column_error(e) or _missing_tools_used_column_error(getattr(e, "orig", e)):
            raise HTTPException(
                status_code=503,
                detail="数据库缺少列 chat_messages.tools_used_json，请在项目根目录执行: alembic upgrade head",
            ) from e
        raise
    return answer, turn_count, notice, tools_used
