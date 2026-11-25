"""会话归属和分页历史；每个操作自行结束短事务。"""
from datetime import datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.chat import CHAT_TIMEZONE, ROLE_NAMES, STATUS_NAMES, MessageStatus
from app.core.chat_errors import ChatBusyError, ChatNotFoundError
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import (
    ChatClearResult, ChatDeleteResult, ChatMessageItem, ChatMessageReference, ChatPageQuery, ChatSessionItem,
)
from app.schemas.auth import UserInfo
from app.services.auth import resolve_current_user

SessionFactory = sessionmaker[Session]
# 新手注意：类型适配器像一张检查表，会逐条核对快照的字段与类型。
REFERENCE_SNAPSHOT_ADAPTER = TypeAdapter(list[ChatMessageReference])


# 做什么：取得与项目数据库时区一致的当前时间
# 入参：无
# 返回：东八区的无时区 datetime，供 MySQL DATETIME 保存
def current_chat_time() -> datetime:
    return datetime.now(CHAT_TIMEZONE).replace(tzinfo=None)


# 做什么：在独立短会话中验证身份，防止鉴权连接陪伴整个流式请求
# 入参：会话工厂和登录凭证
# 返回：公开用户信息；账号或凭证无效时抛既有业务异常
def resolve_chat_user(sessions: SessionFactory, token: str) -> UserInfo:
    with sessions() as db_session:
        return resolve_current_user(db_session, token)


# 做什么：查找当前用户拥有的有效会话
# 入参：数据库会话、用户编号、对话编号和是否加行锁
# 返回：会话模型；不存在或属于其他用户时统一抛 404
def owned_session(db_session: Session, user_id: int, session_id: int, lock: bool = False) -> ChatSession:
    session_query = select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == user_id, ChatSession.is_deleted == 0,
    )
    if lock:
        session_query = session_query.with_for_update()
    chat_session = db_session.scalar(session_query)
    if chat_session is None:
        raise ChatNotFoundError()
    return chat_session


# 做什么：恢复进程退出后遗留的过期生成占用
# 入参：已加锁的会话和同一数据库事务
# 返回：是否恢复；未过期不修改，到期则把助手消息标记中断
def recover_expired(db_session: Session, chat_session: ChatSession) -> bool:
    if not chat_session.active_request_id:
        return False
    if chat_session.generation_expires_at and chat_session.generation_expires_at > current_chat_time():
        return False
    message_query = select(ChatMessage).where(
        ChatMessage.session_id == chat_session.id,
        ChatMessage.request_id == chat_session.active_request_id,
        ChatMessage.status == MessageStatus.GENERATING,
    )
    for message in db_session.scalars(message_query):
        message.status = MessageStatus.INTERRUPTED
        message.error_code = "generation_expired"
    chat_session.active_request_id = None
    chat_session.generation_expires_at = None
    chat_session.history_version += 1
    return True


# 做什么：创建属于当前用户的新对话
# 入参：会话工厂和已验证用户编号
# 返回：新会话的公开结构；数据库故障直接抛出
def create_session(sessions: SessionFactory, user_id: int) -> ChatSessionItem:
    with sessions.begin() as db_session:
        chat_session = ChatSession(user_id=user_id)
        db_session.add(chat_session)
        db_session.flush()
        return public_session(chat_session)


# 做什么：公开本人会话状态，列表中的到期占用不显示为有效生成
# 入参：已校验归属或已按用户过滤的会话模型
# 返回：标题、版本及有效活动请求，不修改数据库
def public_session(chat_session: ChatSession) -> ChatSessionItem:
    session_item = ChatSessionItem.model_validate(chat_session)
    if not session_item.generation_expires_at or session_item.generation_expires_at <= current_chat_time():
        session_item.active_request_id = None
        session_item.generation_expires_at = None
    return session_item


# 做什么：直接查询本人会话的最新状态，并恢复已经到期的占用
# 入参：会话工厂、用户编号和会话编号
# 返回：最新会话状态；不存在或属于他人时抛 404
def get_session_state(sessions: SessionFactory, user_id: int, session_id: int) -> ChatSessionItem:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        recover_expired(db_session, chat_session)
        db_session.flush()
        return public_session(chat_session)


# 做什么：软删除本人一条会话及其中全部消息，正在生成时拒绝
# 入参：会话工厂、用户编号和会话编号
# 返回：删除的消息条数；不存在或属于他人抛 404，生成中抛 409
def delete_session(sessions: SessionFactory, user_id: int, session_id: int) -> ChatDeleteResult:
    with sessions.begin() as db_session:
        # 行锁先拿归属：并发的"发消息"会被挡住，见下面的先后顺序
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        # 占用已过期时先恢复成中断，避免把"其实没在生成"的会话误判成忙
        recover_expired(db_session, chat_session)
        if chat_session.active_request_id:
            raise ChatBusyError()
        deleted_messages = mark_messages_deleted(db_session, [session_id])
        soft_delete_session(chat_session)
        return ChatDeleteResult(session_id=session_id, deleted_messages=deleted_messages)


# 做什么：软删除本人全部有效会话，任一正在生成时整批拒绝
# 入参：会话工厂和用户编号
# 返回：删除的会话与消息条数；生成中抛 409
def clear_sessions(sessions: SessionFactory, user_id: int) -> ChatClearResult:
    with sessions.begin() as db_session:
        # 按编号升序加锁：同一用户的两次清空请求不会互相插队，也不会和发消息形成环
        session_rows = list(db_session.scalars(
            select(ChatSession).where(ChatSession.user_id == user_id, ChatSession.is_deleted == 0)
            .order_by(ChatSession.id).with_for_update()
        ))
        if not session_rows:
            return ChatClearResult(deleted_sessions=0, deleted_messages=0)
        for chat_session in session_rows:
            recover_expired(db_session, chat_session)
        # 先检查再动手：只要有一条真在生成，整批都不删，避免"删到一半失败"
        if any(chat_session.active_request_id for chat_session in session_rows):
            raise ChatBusyError()
        deleted_messages = mark_messages_deleted(db_session, [chat_session.id for chat_session in session_rows])
        for chat_session in session_rows:
            soft_delete_session(chat_session)
        return ChatClearResult(deleted_sessions=len(session_rows), deleted_messages=deleted_messages)


# 做什么：把指定会话下的消息批量标记删除
# 入参：数据库会话和会话编号列表
# 返回：本次实际标记的条数
def mark_messages_deleted(db_session: Session, session_ids: list[int]) -> int:
    result = db_session.execute(
        update(ChatMessage)
        .where(ChatMessage.session_id.in_(session_ids), ChatMessage.is_deleted == 0)
        .values(is_deleted=1)
    )
    return result.rowcount or 0


# 做什么：清掉会话的生成占用并置软删，同时让旧上下文缓存失效
# 入参：已加锁的会话模型
# 返回：无
def soft_delete_session(chat_session: ChatSession) -> None:
    chat_session.is_deleted = 1
    chat_session.active_request_id = None
    chat_session.generation_expires_at = None
    # 版本号加一：Redis 候选缓存键含版本，旧键不可能再被这个会话读到
    chat_session.history_version += 1


# 做什么：分页查询本人有效会话，最近更新的排在前面
# 入参：会话工厂、当前用户编号和分页参数
# 返回：统一分页内容；数据库故障直接抛出
def list_sessions(sessions: SessionFactory, user_id: int, pagination: ChatPageQuery) -> dict:
    with sessions() as db_session:
        conditions = (ChatSession.user_id == user_id, ChatSession.is_deleted == 0)
        total = db_session.scalar(select(func.count()).select_from(ChatSession).where(*conditions))
        session_query = select(ChatSession).where(*conditions).order_by(
            ChatSession.updated_at.desc(), ChatSession.id.desc(),
        ).offset((pagination.page - 1) * pagination.page_size).limit(pagination.page_size)
        items = []
        for chat_session in db_session.scalars(session_query):
            items.append(public_session(chat_session).model_dump(mode="json"))
    return {"items": items, "total": total, "page": pagination.page, "page_size": pagination.page_size}


# 做什么：解码历史快照并核对每条引用是否完整
# 入参：消息表中可为空的 JSON 快照
# 返回：公开引用列表；空值或任一条格式无效时返回空列表
def parse_references(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        references = REFERENCE_SNAPSHOT_ADAPTER.validate_json(raw, strict=True)
    except ValidationError:
        return []
    return [reference.model_dump() for reference in references]


# 做什么：把内部数字角色和状态转换成前端可读的字符串
# 入参：一条消息模型
# 返回：不含上游原始错误和其他用户资料的消息结构
def public_message(message: ChatMessage) -> dict:
    return ChatMessageItem(
        id=message.id, request_id=message.request_id, role=ROLE_NAMES[message.role],
        content=message.content, status=STATUS_NAMES[message.status], model=message.model,
        finish_reason=message.finish_reason, error_code=message.error_code,
        created_at=message.created_at, references=parse_references(message.references_json),
    ).model_dump(mode="json")


# 做什么：按消息编号倒序分页读取本人会话，当前页按时间正序展示
# 入参：会话工厂、用户编号、对话编号和分页参数
# 返回：分页消息及会话信息；跨用户访问统一抛 404
def list_messages(sessions: SessionFactory, user_id: int, session_id: int, pagination: ChatPageQuery) -> dict:
    with sessions.begin() as db_session:
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        recover_expired(db_session, chat_session)
        db_session.flush()
        conditions = (ChatMessage.session_id == session_id, ChatMessage.is_deleted == 0)
        total = db_session.scalar(select(func.count()).select_from(ChatMessage).where(*conditions))
        message_query = select(ChatMessage).where(*conditions).order_by(ChatMessage.id.desc())
        message_query = message_query.offset((pagination.page - 1) * pagination.page_size).limit(pagination.page_size)
        items = []
        for message in reversed(list(db_session.scalars(message_query))):
            items.append(public_message(message))
        session_item = public_session(chat_session).model_dump(mode="json")
    return {"items": items, "total": total, "page": pagination.page,
            "page_size": pagination.page_size, "session": session_item}

