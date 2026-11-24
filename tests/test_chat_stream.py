"""验证流完成、异常和真正的 ASGI 发送断开会持久化正确状态。"""
import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.requests import ClientDisconnect
from starlette.background import BackgroundTask
from sqlalchemy.exc import SQLAlchemyError

from app.core.chat_errors import ModelUnavailableError
from app.llm.client import ModelChunk
from app.services import chat_stream
from app.services.chat_turns import interrupt_turn
from app.core.config import get_settings
from app.core.chat import MessageStatus
from app.models.chat import ChatSession
from app.schemas.chat import TurnResult
from app.utils.sse import ClosingStreamingResponse
from tests.chat_support import chat_sessions, event_items, history_items, new_turn


# 做什么：提供两个正常文本片段和结束原因
# 入参：历史和问题，模拟时不使用
# 返回：不经过网络的模型片段流
async def completed_model(history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "你好"}}]})
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "，世界"}, "finish_reason": "stop"}]})


# 做什么：模拟已经输出部分内容后上游异常
# 入参：历史和问题
# 返回：一个片段后抛安全模型异常
async def broken_model(history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "部分内容"}}]})
    raise ModelUnavailableError()


# 做什么：收集完整的服务事件流
# 入参：会话工厂和生成上下文
# 返回：SSE 文本
async def collect_stream(sessions, turn):
    chunks = []
    async for event in chat_stream.stream_turn(sessions, turn):
        chunks.append(event)
    return "".join(chunks)


# 做什么：确认完成事件在结果保存之后，并且成功消息没有中断错误码
# 入参：隔离会话工厂和模拟替换工具
# 返回：无；错误状态或残留错误码使测试失败
def test_completed_stream_persists_clean_success(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    turn = new_turn(chat_sessions)
    events = event_items(asyncio.run(collect_stream(chat_sessions, turn)))
    assert [event[0] for event in events] == ["started", "delta", "delta", "completed"]
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["content"] == "你好，世界"
    assert answer["status"] == "completed"
    assert answer["error_code"] is None


# 做什么：确认输出后异常被保存为失败，部分内容仍能在历史中查看
# 入参：隔离会话工厂和模拟替换工具
# 返回：无；不能把部分内容标记为完成
def test_upstream_error_preserves_partial_answer(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", broken_model)
    turn = new_turn(chat_sessions)
    events = event_items(asyncio.run(collect_stream(chat_sessions, turn)))
    assert events[-1][0] == "error"
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["status"] == "failed"
    assert answer["content"] == "部分内容"


# 做什么：模拟浏览器在收到部分回答后断开发送通道
# 入参：生成器响应
# 返回：无；真实 ASGI send 抛 OSError，不依赖测试客户端缓冲行为
async def disconnect_response(response):
    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    await response(scope, unused_receive, failing_send)


# 做什么：提供本用例不使用的 ASGI receive
# 入参：无
# 返回：请求体已结束事件
async def unused_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


# 做什么：在首个增量帧发送时模拟网络连接关闭
# 入参：ASGI 响应事件
# 返回：正常事件不处理，增量事件抛 OSError
async def failing_send(message):
    if message["type"] == "http.response.body" and b"event: delta" in message.get("body", b""):
        raise OSError("simulated client disconnect")


# 做什么：确认断开发送后关闭生成器并保存中断状态与部分回答
# 入参：隔离会话工厂和模拟替换工具
# 返回：无；若资源清理失败或消息仍在生成则测试失败
def test_asgi_disconnect_marks_interrupted(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    turn = new_turn(chat_sessions)
    response = ClosingStreamingResponse(chat_stream.stream_turn(chat_sessions, turn))
    with pytest.raises(ClientDisconnect):
        asyncio.run(disconnect_response(response))
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["status"] == "interrupted"
    assert answer["content"] == "你好"


# 做什么：模拟连响应头都未发送成功的断开
# 入参：ASGI 响应事件
# 返回：不返回，直接抛传输失败
async def failing_headers(message):
    raise OSError("simulated failure before generator starts")


# 做什么：执行响应但在启动生成器之前断开
# 入参：响应对象
# 返回：不返回，响应将抛 ClientDisconnect
async def disconnect_before_body(response):
    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    await response(scope, unused_receive, failing_headers)


# 做什么：确认未启动的生成器也有幂等收尾，不留下长期生成占用
# 入参：隔离会话工厂
# 返回：无；空回答应被标记为中断
def test_disconnect_before_first_event_releases_generation(chat_sessions):
    turn = new_turn(chat_sessions)
    cleanup = BackgroundTask(interrupt_turn, chat_sessions, 1, turn.session_id, turn.request_id)
    response = ClosingStreamingResponse(chat_stream.stream_turn(chat_sessions, turn), background=cleanup)
    with pytest.raises(ClientDisconnect):
        asyncio.run(disconnect_before_body(response))
    assert history_items(chat_sessions, turn.session_id)[1]["status"] == "interrupted"


# 做什么：等待足够长的时间以触发总生成超时，并记录是否关闭
# 入参：关闭标记列表、历史和问题
# 返回：仅在未超时时返回一个片段，结束时一定标记关闭
async def waiting_model(closed, history, question):
    try:
        await asyncio.sleep(10)
        yield ModelChunk.model_validate({"choices": [{"delta": {"content": "迟到"}}]})
    finally:
        closed.append(True)


# 做什么：确认等待首字时也受总时限控制，并取消上游请求
# 入参：隔离会话工厂和替换工具
# 返回：无；超时必须明确失败且释放模型生成器
def test_total_timeout_closes_upstream_and_marks_failed(chat_sessions, monkeypatch):
    from functools import partial
    closed = []
    monkeypatch.setattr(chat_stream, "stream_model", partial(waiting_model, closed))
    monkeypatch.setattr(get_settings(), "llm_timeout_seconds", 0.05)
    turn = new_turn(chat_sessions)
    events = event_items(asyncio.run(collect_stream(chat_sessions, turn)))
    assert events[-1][0] == "error"
    assert closed == [True]
    assert history_items(chat_sessions, turn.session_id)[1]["status"] == "failed"
    assert events[-1][1]["code"] == 5008
    assert history_items(chat_sessions, turn.session_id)[1]["error_code"] == "model_timeout"


# 做什么：模拟模型成功之后写主存储失败
# 入参：会话工厂、生成上下文和结果
# 返回：不返回，抛数据库异常
def failing_storage(sessions, turn, result):
    raise SQLAlchemyError("isolated storage failure")


# 做什么：确认落库失败必须发明确错误，不能声称回答已完成保存
# 入参：隔离会话工厂和替换工具
# 返回：无；未发错误事件或发送完成事件均失败
def test_storage_failure_never_reports_completed(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    monkeypatch.setattr(chat_stream, "finish_turn", failing_storage)
    turn = new_turn(chat_sessions)
    events = event_items(asyncio.run(collect_stream(chat_sessions, turn)))
    assert events[-1][0] == "error"
    assert "保存" in events[-1][1]["message"]
    assert "completed" not in [event[0] for event in events]


# 做什么：模拟收到正文后，关闭模型连接又出现异常
# 入参：异常类型、历史和问题
# 返回：一个片段；关闭时抛故障注入异常
async def close_failing_model(error_type, history, question):
    try:
        yield ModelChunk.model_validate({"choices": [{"delta": {"content": "已经收到的部分回答"}}]})
        await asyncio.sleep(10)
    finally:
        raise error_type("private-close-detail")


# 做什么：确认上游关闭异常不能丢弃正文，也不能覆盖原来的传输断开
# 入参：隔离会话工厂、替换工具、日志捕获和异常类型
# 返回：无；正文未保存、占用未释放或敏感异常被记录均失败
@pytest.mark.parametrize("error_type", [RuntimeError, ModelUnavailableError])
def test_disconnect_with_upstream_close_error_preserves_answer(chat_sessions, monkeypatch, caplog, error_type):
    monkeypatch.setattr(chat_stream, "stream_model", partial(close_failing_model, error_type))
    turn = new_turn(chat_sessions)
    cleanup = BackgroundTask(interrupt_turn, chat_sessions, 1, turn.session_id, turn.request_id)
    response = ClosingStreamingResponse(chat_stream.stream_turn(chat_sessions, turn), background=cleanup)
    with pytest.raises((ClientDisconnect, error_type)) as captured:
        asyncio.run(disconnect_response(response))
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["content"] == "已经收到的部分回答"
    assert answer["status"] == "interrupted"
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, turn.session_id).active_request_id is None
    assert isinstance(captured.value, ClientDisconnect)
    assert "清理失败" in caplog.text
    assert "private-close-detail" not in caplog.text


# 做什么：读取一个事件后主动关闭模型编排生成器
# 入参：会话工厂和生成上下文
# 返回：无；清理异常交给当前实现处理
async def close_model_after_first_event(sessions, turn):
    result = TurnResult(status=MessageStatus.INTERRUPTED)
    events = chat_stream.model_events(sessions, turn, result)
    assert await anext(events) == "first-event"
    await events.aclose()


# 做什么：确认片段迭代器清理失败时，上游模型连接仍会被关闭
# 入参：隔离会话工厂和替换工具
# 返回：无；任一资源没有执行关闭则失败
def test_chunk_cleanup_error_still_closes_upstream(chat_sessions, monkeypatch):
    chunks = MagicMock()
    chunks.__aiter__.return_value = ["first-event"]
    chunks.aclose = AsyncMock(side_effect=RuntimeError("private-close-detail"))
    upstream = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(chat_stream, "stream_model", MagicMock(return_value=upstream))
    monkeypatch.setattr(chat_stream, "pump_chunks", MagicMock(return_value=chunks))
    turn = new_turn(chat_sessions)
    asyncio.run(close_model_after_first_event(chat_sessions, turn))
    chunks.aclose.assert_awaited_once()
    upstream.aclose.assert_awaited_once()


# 做什么：确认关闭上游时收到取消异常，仍先保存部分回答并释放占用
# 入参：隔离会话工厂和模拟替换工具
# 返回：无；取消继续传播，正文和中断状态必须保存
def test_upstream_close_cancellation_still_persists_answer(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_stream, "stream_model", partial(close_failing_model, asyncio.CancelledError))
    turn = new_turn(chat_sessions)
    cleanup = BackgroundTask(interrupt_turn, chat_sessions, 1, turn.session_id, turn.request_id)
    response = ClosingStreamingResponse(chat_stream.stream_turn(chat_sessions, turn), background=cleanup)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(disconnect_response(response))
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["content"] == "已经收到的部分回答"
    assert answer["status"] == "interrupted"
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, turn.session_id).active_request_id is None



