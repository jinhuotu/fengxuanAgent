"""缓存预热与成功响应解耦的回归，不访问真实缓存。"""
import asyncio
import logging
from threading import Event, Lock
from time import monotonic
from functools import partial

import pytest

from app.services import chat_stream
from app.services import chat_context, chat_prewarm
from app.core.config import get_settings
from app.core.chat import MessageStatus
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import TurnResult
from app.services.chat_turns import finish_turn, prepare_turn
from tests.test_chat_cache import remember_cache, unexpected_query
from tests.chat_support import chat_sessions, event_items, new_turn
from tests.test_chat_stream import completed_model
from tests.test_chat_api import chat_client, user_headers
from uuid import uuid4

PROBE_WAIT_SECONDS = 3


# 做什么：测试允许建立队列，但所有缓存访问仅使用内存替身
# 入参：配置和模块替换工具
# 返回：无；不访问实际 Redis 或真实数据库
@pytest.fixture(autouse=True)
def isolated_cache_boundary(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_enabled", True)
    monkeypatch.setattr(get_settings(), "chat_prewarm_enabled", True)
    monkeypatch.setattr(chat_context, "read_cache", lambda cache_key: None)
    monkeypatch.setattr(chat_context, "write_cache", lambda cache_key, content, ttl_seconds: None)


class BrokenSubmitter:
    # 做什么：模拟排队自身发生意外异常
    # 入参：会话工厂和版本快照
    # 返回：抛模拟异常，不记录敏感正文
    def submit(self, sessions, turn):
        raise RuntimeError("private-warmup-detail")


# 做什么：收集带可选队列的完整生成流，包含 EOF
# 入参：隔离工厂、请求及队列
# 返回：完整 SSE 文本，不等待后台预热
async def collect_with_queue(sessions, turn, prewarm_queue):
    events = []
    async for event in chat_stream.stream_turn(sessions, turn, prewarm_queue):
        events.append(event)
    return "".join(events)


# 做什么：复现预热故障会阻止已经保存的回答发送完成事件
# 入参：隔离工厂和替换工具
# 返回：无；可选缓存错误导致流失败时测试失败
def test_optional_prewarm_error_cannot_break_completed_stream(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    turn = new_turn(chat_sessions)
    events = event_items(asyncio.run(collect_with_queue(chat_sessions, turn, BrokenSubmitter())))
    assert events[-1][0] == "completed"


# 做什么：在同步线程中阻塞预热，供测试观察响应是否仍可结束
# 入参：启动、释放、完成信号及预热参数
# 返回：释放后正常结束；测试超时也会退出，不遗留永久阻塞线程
def blocking_warmup(started, release, finished, sessions, turn):
    started.set()
    try:
        release.wait(PROBE_WAIT_SECONDS)
    finally:
        finished.set()


# 做什么：确认预热仍阻塞时 completed 和完整流都已返回，数据库已保存
# 入参：隔离工厂和替换工具
# 返回：无；缓存进入关键路径或响应结束前未保存时失败
def test_completed_and_eof_do_not_wait_for_blocked_prewarm(chat_sessions, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(blocking_warmup, started, release, finished))
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    asyncio.run(check_blocked_stream(chat_sessions, started, release, finished))


# 做什么：在受控阻塞预热期间验证持久化、完成事件和 EOF
# 入参：隔离工厂及三个测试信号
# 返回：无；退出前释放测试线程并关闭后台队列
async def check_blocked_stream(sessions, started, release, finished):
    queue = chat_prewarm.PrewarmQueue(2, 1, 0.2)
    queue.start()
    turn = new_turn(sessions)
    try:
        content = await asyncio.wait_for(collect_with_queue(sessions, turn, queue), timeout=1)
        assert event_items(content)[-1][0] == "completed"
        assert await asyncio.to_thread(started.wait, PROBE_WAIT_SECONDS)
        assert not finished.is_set()
        with sessions() as db_session:
            assert db_session.get(ChatMessage, turn.assistant_id).status == MessageStatus.COMPLETED
            assert db_session.get(ChatSession, turn.session_id).active_request_id is None
    finally:
        release.set()
        await queue.close()


# 做什么：确认容量、去重及不可变版本快照，不保存问题正文
# 入参：隔离工厂和替换工具
# 返回：无；满额仍排队、重复任务或快照被修改时失败
def test_queue_is_bounded_deduplicated_and_snapshotted(chat_sessions, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(blocking_warmup, started, release, finished))
    asyncio.run(check_queue_bounds(chat_sessions, started, release))


# 做什么：一个任务运行、一个排队时验证拒绝第三个任务及重复快照
# 入参：隔离工厂及工作启动和释放信号
# 返回：无；测试结束会释放预热线程
async def check_queue_bounds(sessions, started, release):
    queue = chat_prewarm.PrewarmQueue(1, 1, 0.2)
    queue.start()
    turn = new_turn(sessions, "private-question")
    try:
        assert queue.submit(sessions, turn)
        assert await asyncio.to_thread(started.wait, PROBE_WAIT_SECONDS)
        assert not queue.submit(sessions, turn)
        following = turn.model_copy(update={"history_version": 1, "request_id": str(uuid4())})
        assert queue.submit(sessions, following)
        assert not queue.submit(sessions, turn.model_copy(update={"history_version": 2}))
        queued_job = queue.queue.get_nowait()
        assert queued_job.turn is not following
        assert queued_job.turn.content == ""
        assert following.content == "private-question"
        queue.pending_keys.discard(queued_job.key)
        queue.queue.task_done()
    finally:
        release.set()
        await queue.close()
    assert not queue.submit(sessions, turn)


# 做什么：记录同时执行的预热数，阻塞到测试释放
# 入参：计数状态、线程锁、启动/释放信号及任务参数
# 返回：退出时减少活动数，不访问网络
def counted_warmup(counts, lock, started, release, sessions, turn):
    with lock:
        counts["active"] += 1
        counts["maximum"] = max(counts["maximum"], counts["active"])
        if counts["active"] == 2:
            started.set()
    try:
        release.wait(PROBE_WAIT_SECONDS)
    finally:
        with lock:
            counts["active"] -= 1


# 做什么：确认工作任务数限制实际线程调用，不随请求数量增长
# 入参：隔离工厂和替换工具
# 返回：无；超过两个同时执行时失败
def test_worker_count_limits_running_jobs(chat_sessions, monkeypatch):
    counts = {"active": 0, "maximum": 0}
    started, release = Event(), Event()
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(counted_warmup, counts, Lock(), started, release))
    asyncio.run(check_worker_limit(chat_sessions, counts, started, release))


# 做什么：排入六个不同版本任务并检查仅两个开始执行
# 入参：隔离工厂、活动计数和测试信号
# 返回：无；退出时释放线程并等待记账完成
async def check_worker_limit(sessions, counts, started, release):
    queue = chat_prewarm.PrewarmQueue(10, 2, 0.2)
    queue.start()
    turn = new_turn(sessions)
    try:
        for version in range(6):
            assert queue.submit(sessions, turn.model_copy(update={"history_version": version}))
        assert await asyncio.to_thread(started.wait, PROBE_WAIT_SECONDS)
        assert counts["active"] == 2
        assert queue.queue.qsize() == 4
        release.set()
        await asyncio.wait_for(queue.queue.join(), timeout=1)
        assert counts["maximum"] == 2
    finally:
        release.set()
        await queue.close()


# 做什么：第一次预热抛故障，第二次继续执行
# 入参：调用记录、会话工厂和快照
# 返回：首次抛模拟异常，其余正常结束
def failing_first_warmup(calls, sessions, turn):
    calls.append(turn.request_id)
    if len(calls) == 1:
        raise RuntimeError("private-cache-failure")


# 做什么：确认预热异常隔离且日志脱敏，工作任务继续处理后续任务
# 入参：隔离工厂、替换工具和日志捕获
# 返回：无；异常原文泄露或工作任务退出时失败
def test_failed_job_does_not_stop_worker(chat_sessions, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(failing_first_warmup, calls))
    with caplog.at_level(logging.WARNING, logger=chat_prewarm.__name__):
        asyncio.run(run_two_jobs(chat_sessions))
    assert len(calls) == 2
    assert "RuntimeError" in caplog.text
    assert "private-cache-failure" not in caplog.text


# 做什么：按顺序执行两个不同版本任务，并正常回收队列
# 入参：隔离工厂
# 返回：无；后台任务超时则测试失败
async def run_two_jobs(sessions):
    queue = chat_prewarm.PrewarmQueue(2, 1, 0.2)
    queue.start()
    turn = new_turn(sessions)
    try:
        assert queue.submit(sessions, turn)
        assert queue.submit(sessions, turn.model_copy(update={"history_version": 1, "request_id": str(uuid4())}))
        await asyncio.wait_for(queue.queue.join(), timeout=1)
    finally:
        await queue.close()


# 做什么：确认关闭丢弃积压，限时停止等待，并回收所有异步工作任务
# 入参：隔离工厂和替换工具
# 返回：无；关闭一直等待阻塞任务或接受新任务时失败
def test_shutdown_discards_waiting_jobs_and_bounds_wait(chat_sessions, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(blocking_warmup, started, release, finished))
    asyncio.run(check_bounded_shutdown(chat_sessions, started, release, finished))


# 做什么：真实阻塞一个同步调用，验证关闭超时后不再等待它
# 入参：隔离工厂及工作启动/释放/完成信号
# 返回：无；退出测试前释放仍在执行的线程，避免遗留测试资源
async def check_bounded_shutdown(sessions, started, release, finished):
    queue = chat_prewarm.PrewarmQueue(2, 1, 0.03)
    queue.start()
    turn = new_turn(sessions)
    try:
        assert queue.submit(sessions, turn)
        assert await asyncio.to_thread(started.wait, PROBE_WAIT_SECONDS)
        assert queue.submit(sessions, turn.model_copy(update={"history_version": 1}))
        started_at = monotonic()
        await queue.close()
        assert monotonic() - started_at < 0.5
        assert not finished.is_set()
        assert queue.queue.empty()
        assert not queue.workers
        assert not queue.submit(sessions, turn)
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, PROBE_WAIT_SECONDS)
        await queue.close()


# 做什么：确认失败或未保存的回答不会提交预热
# 入参：隔离工厂和结果状态
# 返回：无；不满足成功条件仍入队时失败
@pytest.mark.parametrize("saved,status", [(None, MessageStatus.COMPLETED), (False, MessageStatus.COMPLETED), (True, MessageStatus.FAILED)])
def test_only_persisted_success_can_enqueue(chat_sessions, monkeypatch, saved, status):
    submitted = []
    monkeypatch.setattr(chat_stream, "enqueue_prewarm", lambda queue, sessions, turn: submitted.append(turn))
    turn = new_turn(chat_sessions)
    asyncio.run(chat_stream.final_event(chat_sessions, turn, TurnResult(status=status), saved, "失败"))
    assert submitted == []


# 做什么：确认迟到的旧预热只写旧版本，下一问使用较新成功历史
# 入参：隔离工厂和替换工具
# 返回：无；旧版本覆盖新版本或命中时回源则失败
def test_out_of_order_prewarm_keeps_history_versions_separate(chat_sessions, monkeypatch):
    storage = {}
    monkeypatch.setattr(chat_context, "read_cache", storage.get)
    monkeypatch.setattr(chat_context, "write_cache", partial(remember_cache, storage))
    first = new_turn(chat_sessions)
    finish_turn(chat_sessions, first, TurnResult(status=MessageStatus.COMPLETED, content="第一答"))
    second = prepare_turn(chat_sessions, 1, first.session_id, str(uuid4()), "第二问")
    finish_turn(chat_sessions, second, TurnResult(status=MessageStatus.COMPLETED, content="第二答"))
    chat_prewarm.run_prewarm_job(chat_prewarm.PrewarmJob(chat_sessions, second))
    chat_prewarm.run_prewarm_job(chat_prewarm.PrewarmJob(chat_sessions, first))
    following = prepare_turn(chat_sessions, 1, first.session_id, str(uuid4()), "第三问")
    monkeypatch.setattr(chat_context, "query_context", unexpected_query)
    assert [message.content for message in chat_context.load_context(chat_sessions, following)] == ["测试问题", "第一答", "第二问", "第二答"]


# 做什么：确认真实接口从应用状态取得队列，预热阻塞时 HTTP 响应仍结束
# 入参：真实测试客户端和替换工具
# 返回：无；队列未接入或响应等待缓存时失败
def test_http_response_finishes_before_prewarm(chat_client, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    monkeypatch.setattr(chat_prewarm, "warm_context", partial(blocking_warmup, started, release, finished))
    headers = user_headers(1)
    session_id = chat_client.post("/chat/sessions", headers=headers).json()["data"]["id"]
    try:
        response = chat_client.post(f"/chat/sessions/{session_id}/messages", headers=headers,
                                   json={"request_id": str(uuid4()), "content": "测试"})
        assert "event: completed" in response.text
        assert started.wait(PROBE_WAIT_SECONDS)
        assert not finished.is_set()
    finally:
        release.set()
        finished.wait(PROBE_WAIT_SECONDS)


# 做什么：确认应用生命周期托管队列和固定工作任务，退出后无遗留异步任务
# 入参：配置替换工具
# 返回：无；状态未清理或工作任务未关闭时失败
def test_app_lifespan_manages_prewarm_queue(monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_prewarm_workers", 2)
    asyncio.run(check_app_prewarm_lifespan())


# 做什么：进入实际应用生命周期，检查队列可用并退出回收
# 入参：无，使用测试插件的虚拟配置
# 返回：无；不向队列提交任务，因此不访问实际数据库
async def check_app_prewarm_lifespan():
    from app.main import app, lifespan
    async with lifespan(app):
        queue = app.state.chat_prewarm_queue
        assert queue.accepting
        assert len(queue.workers) == 2
    assert app.state.chat_prewarm_queue is None
    for pending_task in asyncio.all_tasks():
        assert not pending_task.get_name().startswith(chat_prewarm.PREWARM_TASK_PREFIX)


# 做什么：确认 Redis 或预热开关停用时不创建后台任务
# 入参：配置替换工具及停用字段
# 返回：无；停用后仍有队列时失败
@pytest.mark.parametrize("disabled_field", ["redis_enabled", "chat_prewarm_enabled"])
def test_disabled_prewarm_has_no_workers(monkeypatch, disabled_field):
    monkeypatch.setattr(get_settings(), disabled_field, False)
    asyncio.run(check_disabled_queue())


# 做什么：检查停用的生命周期只返回 None
# 入参：无
# 返回：无；生命周期创建了队列则失败
async def check_disabled_queue():
    async with chat_prewarm.prewarm_lifespan() as queue:
        assert queue is None
