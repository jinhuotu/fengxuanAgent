"""生成请求的事务边界、去重和中断；数据库占用只持续短事务。"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.chat import CHAT_STREAM_DEADLINE_MARGIN_SECONDS, CHAT_TITLE_LENGTH, MessageRole, MessageStatus
from app.core.chat_errors import ChatBusyError, ChatDuplicateError, ModelNotConfiguredError
from app.core.config import get_settings
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import PreparedTurn, TurnResult
from app.services.chat_history import SessionFactory, current_chat_time, owned_session, recover_expired
from app.services.chat_capacity import ensure_generation_capacity, lock_generation_gate
from app.services.chat_requests import validate_retry
from app.llm import client as model_client
from app.llm.tokens import ensure_request_budget

logger = logging.getLogger(__name__)


# 做什么：保存问题和助手占位消息，同时设置本次生成占用
# 入参：已加锁的会话、事务、请求编号与问题正文
# 返回：助手消息模型；写入失败直接抛出，由事务回滚
def insert_turn(db_session: Session, chat_session: ChatSession, request_id: str, content: str) -> ChatMessage:
    settings = get_settings()
    db_session.add(ChatMessage(session_id=chat_session.id, request_id=request_id,
                               role=MessageRole.USER, content=content, status=MessageStatus.COMPLETED))
    assistant = ChatMessage(session_id=chat_session.id, request_id=request_id,
                            role=MessageRole.ASSISTANT, status=MessageStatus.GENERATING, model=settings.llm_model)
    db_session.add(assistant)
    if chat_session.history_version == 0:
        chat_session.title = content[:CHAT_TITLE_LENGTH]
    chat_session.active_request_id = request_id
    chat_session.updated_at = current_chat_time()
    lease_seconds = settings.llm_timeout_seconds + CHAT_STREAM_DEADLINE_MARGIN_SECONDS
    chat_session.generation_expires_at = current_chat_time() + timedelta(seconds=lease_seconds)
    db_session.flush()
    return assistant


# 做什么：校验归属和去重，接收一次新的生成请求
# 入参：会话工厂、当前用户、会话、客户端请求编号和问题
# 返回：不持有数据库连接的生成上下文；无权、重复、忙或未配置时抛业务异常
def prepare_turn(sessions: SessionFactory, user_id: int, session_id: int, request_id: str, content: str,
                 retry_of_request_id: str | None = None) -> PreparedTurn:
    with sessions.begin() as db_session:
        lock_generation_gate(db_session)
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        recover_expired(db_session, chat_session)
        duplicate_query = select(ChatMessage.id).where(
            ChatMessage.session_id == session_id, ChatMessage.request_id == request_id,
        ).limit(1)
        if db_session.scalar(duplicate_query) is not None:
            raise ChatDuplicateError()
        if chat_session.active_request_id:
            raise ChatBusyError()
        if not get_settings().llm_api_key.get_secret_value().strip():
            raise ModelNotConfiguredError()
        ensure_request_budget(model_client.SYSTEM_PROMPT, [], content)
        validate_retry(db_session, chat_session, retry_of_request_id, content)
        ensure_generation_capacity(db_session)
        assistant = insert_turn(db_session, chat_session, request_id, content)
        return PreparedTurn(user_id=user_id, session_id=session_id, request_id=request_id,
                            assistant_id=assistant.id, history_version=chat_session.history_version, content=content,
                            knowledge_base_id=chat_session.knowledge_base_id)


# 做什么：只允许当前请求保存最终结果，防止旧请求覆盖新请求
# 入参：会话工厂、生成上下文和最终结果
# 返回：本次成功结束占用返回 True；请求已中断或被替代返回 False
def finish_turn(sessions: SessionFactory, turn: PreparedTurn, result: TurnResult) -> bool:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, turn.user_id, turn.session_id, lock=True)
        assistant = db_session.get(ChatMessage, turn.assistant_id)
        if chat_session.active_request_id != turn.request_id:
            preserve_interrupted_content(assistant, result.content)
            return False
        assistant.content = result.content
        assistant.status = result.status
        assistant.prompt_tokens = result.prompt_tokens
        assistant.completion_tokens = result.completion_tokens
        assistant.finish_reason = result.finish_reason
        assistant.error_code = result.error_code
        chat_session.active_request_id = None
        chat_session.generation_expires_at = None
        chat_session.history_version += 1
        chat_session.updated_at = current_chat_time()
    logger.info("生成结束 | session_id=%s | request_id=%s | status=%s",
                turn.session_id, turn.request_id, result.status)
    return True


# 做什么：显式中断先到达时，仍补存流中已经收到的部分文字
# 入参：助手消息和该请求实际收到的部分正文
# 返回：无；只补长中断消息，不改变新请求或已完成结果
def preserve_interrupted_content(assistant: ChatMessage, content: str) -> None:
    if assistant.status == MessageStatus.INTERRUPTED and len(content) > len(assistant.content):
        assistant.content = content


# 做什么：在短事务内保存正在生成的正文快照，不结束当前请求
# 入参：会话工厂、生成上下文和已收到的正文
# 返回：仍属当前请求返回 True，已中断或状态变化返回 False；数据库故障抛出
def save_partial_answer(sessions: SessionFactory, turn: PreparedTurn, content: str) -> bool:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, turn.user_id, turn.session_id, lock=True)
        if chat_session.active_request_id != turn.request_id:
            return False
        assistant_query = select(ChatMessage).where(
            ChatMessage.id == turn.assistant_id, ChatMessage.session_id == turn.session_id,
            ChatMessage.request_id == turn.request_id, ChatMessage.role == MessageRole.ASSISTANT,
            ChatMessage.status == MessageStatus.GENERATING, ChatMessage.is_deleted == 0,
        )
        assistant = db_session.scalar(assistant_query)
        if assistant is None:
            return False
        # 延迟到达的短快照不能把已经保存的长正文覆盖回去。
        if len(content) > len(assistant.content):
            assistant.content = content
    return True


# 做什么：停止本人会话中的指定请求，重复中断安全无副作用
# 入参：会话工厂、当前用户编号、会话编号与请求编号
# 返回：是否实际中断；跨用户访问抛 404，已完成请求返回 False
def interrupt_turn(sessions: SessionFactory, user_id: int, session_id: int, request_id: str) -> bool:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        if chat_session.active_request_id != request_id:
            return False
        assistant_query = select(ChatMessage).where(
            ChatMessage.session_id == session_id, ChatMessage.request_id == request_id,
            ChatMessage.role == MessageRole.ASSISTANT,
        )
        assistant = db_session.scalar(assistant_query)
        assistant.status = MessageStatus.INTERRUPTED
        assistant.error_code = "client_disconnected"
        chat_session.active_request_id = None
        chat_session.generation_expires_at = None
        chat_session.history_version += 1
        chat_session.updated_at = current_chat_time()
    return True


# 做什么：流式心跳时确认生成占用仍属于本请求
# 入参：会话工厂和生成上下文
# 返回：仍可生成返回 True；显式中断或占用变化返回 False
def turn_is_active(sessions: SessionFactory, turn: PreparedTurn) -> bool:
    with sessions() as db_session:
        chat_session = owned_session(db_session, turn.user_id, turn.session_id)
        return chat_session.active_request_id == turn.request_id

