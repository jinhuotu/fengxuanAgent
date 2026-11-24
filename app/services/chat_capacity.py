"""全站生成准入控制，复用会话占用，不增加内存或 Redis 计数器。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.chat import CHAT_GENERATION_GATE_ID
from app.core.chat_errors import ChatCapacityError, ChatCapacityUnavailableError
from app.core.config import get_settings
from app.models.chat import ChatGenerationGate, ChatSession
from app.services.chat_history import current_chat_time


# 做什么：锁住固定准入记录，让不同进程依次完成短事务中的检查和占用
# 入参：尚未执行其他查询的生成接收事务
# 返回：无；固定记录缺失时拒绝接收，禁止绕过全站限制
def lock_generation_gate(db_session: Session) -> None:
    # 新手注意：准入锁像取号台，一次只放一个人检查名额，回答生成时不占着取号台。
    gate_query = select(ChatGenerationGate.id).where(
        ChatGenerationGate.id == CHAT_GENERATION_GATE_ID, ChatGenerationGate.is_deleted == 0,
    ).with_for_update()
    if db_session.scalar(gate_query) is None:
        raise ChatCapacityUnavailableError()


# 做什么：校验全站有效生成占用是否已达到上限
# 入参：已持有准入锁的事务
# 返回：有名额时正常返回，满额抛 429；过期或损坏占用不计数
def ensure_generation_capacity(db_session: Session) -> None:
    active_query = select(func.count()).select_from(ChatSession).where(
        ChatSession.is_deleted == 0, ChatSession.active_request_id.is_not(None),
        ChatSession.generation_expires_at > current_chat_time(),
    )
    if db_session.scalar(active_query) >= get_settings().chat_global_concurrency_limit:
        raise ChatCapacityError()
