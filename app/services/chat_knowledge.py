"""在短事务内修改本人的会话选库。"""
from sqlalchemy.orm.attributes import flag_modified

from app.core.chat_errors import ChatBusyError
from app.core.errors import KnowledgeBaseNotFoundError
from app.schemas.chat import ChatSessionItem
from app.services.chat_history import SessionFactory, owned_session, public_session
from app.services.knowledge import require_owned_knowledge_base


# 做什么：为本人的空闲会话选择或清除知识库
# 入参：会话工厂、用户编号、会话编号和可为空的知识库编号
# 返回：更新后的会话公开字段；会话不可见、忙碌或知识库不可见时抛对应异常
def set_session_knowledge_base(
    sessions: SessionFactory, user_id: int, session_id: int, knowledge_base_id: int | None
) -> ChatSessionItem:
    with sessions.begin() as db_session:
        base_error = None
        if knowledge_base_id is not None:
            # 与删库保持相同的加锁顺序，避免彼此等待。会话自身错误仍优先返回。
            try:
                require_owned_knowledge_base(db_session, user_id, knowledge_base_id)
            except KnowledgeBaseNotFoundError as error:
                base_error = error
        chat_session = owned_session(db_session, user_id, session_id, lock=True)
        if chat_session.active_request_id is not None:
            raise ChatBusyError()
        if base_error is not None:
            raise base_error
        if chat_session.knowledge_base_id != knowledge_base_id:
            chat_session.knowledge_base_id = knowledge_base_id
            # 显式保留更新时间，避免 ORM 的 onupdate 改变会话排序。
            flag_modified(chat_session, "updated_at")
            db_session.flush()
        return public_session(chat_session)
