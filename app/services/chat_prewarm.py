"""有界、尽力而为的缓存预热队列，不能阻断成功回答。"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic

from app.core.config import get_settings
from app.schemas.chat import PreparedTurn
from app.services.chat_context import warm_context
from app.services.chat_history import SessionFactory

logger = logging.getLogger(__name__)
PREWARM_TASK_PREFIX = "chat-prewarm-"
PrewarmKey = tuple[SessionFactory, int, int, int]


@dataclass(frozen=True)
class PrewarmJob:
    sessions: SessionFactory
    turn: PreparedTurn

    # 做什么：取得该数据源、用户、会话和下一历史版本的去重标识
    # 入参：无
    # 返回：不可变标识，不含问题或回答正文
    @property
    def key(self) -> PrewarmKey:
        return (self.sessions, self.turn.user_id, self.turn.session_id, self.turn.history_version + 1)


# 做什么：在同步线程内独立预热并记录安全结果，不影响生成状态
# 入参：只携带工厂及版本快照的任务
# 返回：无；普通异常记录类型，正文、凭证和异常原文不进入日志
def run_prewarm_job(warmup_job: PrewarmJob) -> None:
    started_at = monotonic()
    turn = warmup_job.turn
    try:
        warm_context(warmup_job.sessions, turn)
        logger.info("缓存预热结束 | user_id=%s | session_id=%s | request_id=%s | history_version=%s | elapsed_seconds=%.2f",
                    turn.user_id, turn.session_id, turn.request_id, turn.history_version + 1, monotonic() - started_at)
    except Exception as error:
        logger.warning("跳过失败的缓存预热 | user_id=%s | session_id=%s | request_id=%s | error_type=%s",
                       turn.user_id, turn.session_id, turn.request_id, type(error).__name__)


class PrewarmQueue:
    # 做什么：建立有容量上限的待执行队列，不在构造时启动线程或查询数据库
    # 入参：排队容量、工作任务数和关闭等待秒数
    # 返回：未启动的队列对象
    def __init__(self, capacity: int, worker_count: int, shutdown_seconds: float) -> None:
        # 新手注意：队列像数量有限的候诊座位，满了就跳过可选预热，不让用户排队等缓存。
        self.queue: asyncio.Queue[PrewarmJob] = asyncio.Queue(maxsize=capacity)
        self.worker_count = worker_count
        self.shutdown_seconds = shutdown_seconds
        self.accepting = False
        self.started = False
        self.pending_keys: set[PrewarmKey] = set()
        self.workers: list[asyncio.Task] = []

    # 做什么：启动固定数量的后台工作任务
    # 入参：无
    # 返回：无；重复启动直接拒绝，避免额外工作任务失去管理
    def start(self) -> None:
        if self.started:
            raise RuntimeError("预热队列不能重复启动")
        self.started = True
        self.accepting = True
        for worker_number in range(self.worker_count):
            self.workers.append(asyncio.create_task(self.work(), name=f"{PREWARM_TASK_PREFIX}{worker_number}"))

    # 做什么：立即尝试排队，不等待空位，复制版本快照并去除不需要的问题正文
    # 入参：会话工厂和刚完成的生成上下文
    # 返回：成功排队为 True；关闭中、重复或满额为 False
    def submit(self, sessions: SessionFactory, turn: PreparedTurn) -> bool:
        if not self.accepting:
            return False
        snapshot = turn.model_copy(update={"content": ""}, deep=True)
        warmup_job = PrewarmJob(sessions, snapshot)
        if warmup_job.key in self.pending_keys:
            return False
        try:
            self.queue.put_nowait(warmup_job)
        except asyncio.QueueFull:
            logger.info("缓存预热队列已满，跳过 | user_id=%s | session_id=%s | request_id=%s",
                        turn.user_id, turn.session_id, turn.request_id)
            return False
        self.pending_keys.add(warmup_job.key)
        return True

    # 做什么：顺序消费单个工作任务的预热，实际阻塞操作不占事件循环
    # 入参：无
    # 返回：持续运行至关闭取消，每个出队任务都释放记账
    async def work(self) -> None:
        while True:
            warmup_job = await self.queue.get()
            try:
                await asyncio.to_thread(run_prewarm_job, warmup_job)
            finally:
                self.pending_keys.discard(warmup_job.key)
                self.queue.task_done()

    # 做什么：丢弃未执行任务，关闭不必为可重建缓存清空积压
    # 入参：无
    # 返回：丢弃数量，不影响已经开始执行的任务
    def discard_waiting(self) -> int:
        discarded_count = 0
        while True:
            try:
                warmup_job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return discarded_count
            self.pending_keys.discard(warmup_job.key)
            self.queue.task_done()
            discarded_count += 1

    # 做什么：停止接收，丢弃排队任务，限时等待正在执行的预热并回收异步任务
    # 入参：无
    # 返回：无；超时后停止等待，同步 I/O 仍由其自身超时结束
    async def close(self) -> None:
        self.accepting = False
        discarded_count = self.discard_waiting()
        try:
            await asyncio.wait_for(self.queue.join(), timeout=self.shutdown_seconds)
        except TimeoutError:
            logger.warning("缓存预热关闭等待到期，停止等待同步调用 | discarded_count=%s", discarded_count)
        finally:
            for worker in self.workers:
                worker.cancel()
            await asyncio.gather(*self.workers, return_exceptions=True)
            self.workers.clear()


# 做什么：成功保存后尽力提交可选预热，排队异常不能阻断 completed 或 SSE 结束
# 入参：可选队列、会话工厂和已完成上下文
# 返回：是否排队；缺少队列、停用、满额或异常均返回 False
def enqueue_prewarm(prewarm_queue: PrewarmQueue | None, sessions: SessionFactory, turn: PreparedTurn) -> bool:
    if prewarm_queue is None or not get_settings().redis_enabled or not get_settings().chat_prewarm_enabled:
        return False
    try:
        return prewarm_queue.submit(sessions, turn)
    except Exception as error:
        logger.warning("缓存预热提交失败，回答仍已保存 | user_id=%s | session_id=%s | request_id=%s | error_type=%s",
                       turn.user_id, turn.session_id, turn.request_id, type(error).__name__)
        return False


# 做什么：随应用启停管理每个进程的预热队列，Redis 停用时不创建任务
# 入参：无，读取统一配置
# 返回：可用队列或 None；退出时停止接收并关闭任务
@asynccontextmanager
async def prewarm_lifespan() -> AsyncIterator[PrewarmQueue | None]:
    settings = get_settings()
    if not settings.redis_enabled or not settings.chat_prewarm_enabled:
        yield None
        return
    prewarm_queue = PrewarmQueue(settings.chat_prewarm_queue_capacity, settings.chat_prewarm_workers,
                                 settings.chat_prewarm_shutdown_seconds)
    prewarm_queue.start()
    try:
        yield prewarm_queue
    finally:
        await prewarm_queue.close()
