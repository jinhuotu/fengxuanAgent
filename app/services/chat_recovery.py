"""随应用启停的过期占用恢复；不依赖 Redis 或外部调度器。"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.models.chat import ChatSession
from app.services.chat_history import SessionFactory, current_chat_time, recover_expired

logger = logging.getLogger(__name__)
RECOVERY_TASK_NAME = "chat-expired-recovery"


# 做什么：在一个有界短事务中恢复已到期的有效会话
# 入参：会话工厂和本轮最大处理数
# 返回：恢复数量；数据库异常抛给后台任务以便记录和重试
def recover_expired_batch(sessions: SessionFactory, batch_size: int) -> int:
    with sessions.begin() as db_session:
        session_query = select(ChatSession).where(
            ChatSession.is_deleted == 0,
            ChatSession.active_request_id.is_not(None),
            or_(ChatSession.generation_expires_at <= current_chat_time(),
                ChatSession.generation_expires_at.is_(None)),
        ).order_by(ChatSession.id).limit(batch_size).with_for_update(skip_locked=True)
        # 新手注意：SKIP LOCKED 像跳过正在办理的窗口，多进程不排队争抢同一批记录。
        recovered_count = 0
        for chat_session in db_session.scalars(session_query):
            # 行锁内再次检查到期时间，不能依据锁外的旧请求状态清理占用。
            if recover_expired(db_session, chat_session):
                recovered_count += 1
    return recovered_count


# 做什么：执行一轮恢复并隔离数据库故障，不阻断服务启动
# 入参：会话工厂和批量上限
# 返回：无；数据库异常记录安全日志，下一轮继续尝试
async def recover_once(sessions: SessionFactory, batch_size: int) -> None:
    try:
        recovered_count = await run_in_threadpool(recover_expired_batch, sessions, batch_size)
        if recovered_count:
            logger.info("过期生成恢复完成 | recovered_count=%s", recovered_count)
    except SQLAlchemyError as error:
        logger.error("过期生成恢复失败，将于下一轮重试 | error_type=%s", type(error).__name__)


# 做什么：启动即扫描，之后定期恢复，关闭时停止等待
# 入参：会话工厂和关闭信号
# 返回：无；关闭时等待正在执行的短事务结束
async def recovery_loop(sessions: SessionFactory, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        await recover_once(sessions, settings.chat_recovery_batch_size)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.chat_recovery_interval_seconds)
        except TimeoutError:
            continue


# 做什么：将恢复任务的创建和回收绑定到应用生命周期
# 入参：应用当前使用的同步会话工厂
# 返回：异步上下文；禁用时不创建任务，退出时等待任务结束
@asynccontextmanager
async def recovery_lifespan(sessions: SessionFactory) -> AsyncIterator[None]:
    if not get_settings().chat_recovery_enabled:
        yield
        return
    stop_event = asyncio.Event()
    recovery_task = asyncio.create_task(recovery_loop(sessions, stop_event), name=RECOVERY_TASK_NAME)
    try:
        yield
    finally:
        stop_event.set()
        await recovery_task
