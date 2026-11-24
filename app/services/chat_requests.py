"""定位一轮已提交请求，并在会话行锁内校验手动重试资格。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.chat import MessageRole, MessageStatus
from app.core.chat_errors import ChatRequestNotFoundError, ChatRetryNotAllowedError
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import ChatRequestResult
from app.services.chat_history import SessionFactory, owned_session, public_message, recover_expired

REQUEST_MESSAGE_COUNT = 2
RETRYABLE_MESSAGE_STATUSES = {MessageStatus.FAILED, MessageStatus.INTERRUPTED}


# 做什么：定位指定会话中的一对问题和回答，不依赖历史分页
# 入参：当前数据库事务、会话编号和请求编号
# 返回：问题和回答；记录未提交、不完整或软删除时抛 404
def request_messages(db_session: Session, session_id: int, request_id: str) -> tuple[ChatMessage, ChatMessage]:
    message_query = select(ChatMessage).where(
        ChatMessage.session_id == session_id, ChatMessage.request_id == request_id,
        ChatMessage.is_deleted == 0,
    ).order_by(ChatMessage.role).limit(REQUEST_MESSAGE_COUNT)
    messages = list(db_session.scalars(message_query))
    if len(messages) != REQUEST_MESSAGE_COUNT:
        raise ChatRequestNotFoundError()
    question, answer = messages
    if question.role != MessageRole.USER or answer.role != MessageRole.ASSISTANT:
        raise ChatRequestNotFoundError()
    return question, answer


# 做什么：判断该轮是否为当前会话最近的可重试问题
# 入参：已加锁会话、数据库事务及该轮消息
# 返回：没有活动占用且最近回答失败或中断时返回 True
def request_can_retry(db_session: Session, chat_session: ChatSession, question: ChatMessage, answer: ChatMessage) -> bool:
    if chat_session.active_request_id or answer.status not in RETRYABLE_MESSAGE_STATUSES:
        return False
    latest_query = select(ChatMessage.id).where(
        ChatMessage.session_id == chat_session.id, ChatMessage.role == MessageRole.USER,
        ChatMessage.is_deleted == 0,
    ).order_by(ChatMessage.id.desc()).limit(1)
    return db_session.scalar(latest_query) == question.id


# 做什么：查询本人请求结果，并恢复已经到期的占用
# 入参：会话工厂、用户编号、会话编号和请求编号
# 返回：问题、回答、忙状态和重试资格；越权或不存在时抛 404
def get_request_result(sessions: SessionFactory, user_id: int, session_id: int, request_id: str) -> ChatRequestResult:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        recover_expired(db_session, chat_session)
        db_session.flush()
        question, answer = request_messages(db_session, session_id, request_id)
        return ChatRequestResult(
            request_id=request_id, question=public_message(question), answer=public_message(answer),
            session_busy=bool(chat_session.active_request_id),
            can_retry=request_can_retry(db_session, chat_session, question, answer),
            history_version=chat_session.history_version, active_request_id=chat_session.active_request_id,
        )


# 做什么：在接收事务内再次验证重试，防止查询之后状态变化造成旧问题重放
# 入参：已加锁会话、事务、原请求编号和重试正文
# 返回：无；非最近失败问题或正文不一致时抛 409
def validate_retry(db_session: Session, chat_session: ChatSession, request_id: str | None, content: str) -> None:
    if request_id is None:
        return
    question, answer = request_messages(db_session, chat_session.id, request_id)
    if not request_can_retry(db_session, chat_session, question, answer) or question.content != content:
        raise ChatRetryNotAllowedError()
