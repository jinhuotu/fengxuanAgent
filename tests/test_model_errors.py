"""模型错误分类及持久化回归，不连接第三方服务。"""
import asyncio
import logging
from functools import partial

import httpx
import pytest

from app.core.chat_errors import ModelUnavailableError
from app.llm import client as model_client
from app.llm.client import ModelChunk, response_events, send_attempt
from app.core.config import get_settings
from app.models.chat import ChatSession
from app.services import chat_stream
from tests.chat_support import chat_sessions, event_items, history_items, new_turn
from tests.test_chat_stream import collect_stream

HTTP_ERROR_CASES = [
    (401, "model_authentication_failed", 5003),
    (403, "model_permission_denied", 5004),
    (429, "model_rate_limited", 5005),
    (402, "model_payment_required", 5006),
    (400, "model_request_rejected", 5007),
    (404, "model_request_rejected", 5007),
    (422, "model_request_rejected", 5007),
    (500, "model_unavailable", 5001),
    (503, "model_unavailable", 5001),
    (408, "model_timeout", 5008),
    (504, "model_timeout", 5008),
]


# 做什么：使用本地 HTTP 模拟器捕获安全模型异常
# 入参：上游 HTTP 状态
# 返回：分类异常；意外成功使测试失败
async def capture_http_error(status_code):
    transport = httpx.MockTransport(partial(provider_failure, status_code))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelUnavailableError) as captured:
            await send_attempt(client, {}, False)
    return captured.value


# 做什么：返回含敏感正文的模拟供应商错误
# 入参：HTTP 状态和模拟请求
# 返回：本地错误响应
def provider_failure(status_code, request):
    return httpx.Response(status_code, text="private-provider-key-and-prompt", request=request)


# 做什么：确认 HTTP 错误不再全部折叠为模型不可用
# 入参：HTTP 状态、历史错误标识和事件码
# 返回：无；分类或脱敏错误则失败
@pytest.mark.parametrize("status_code,error_name,event_code", HTTP_ERROR_CASES)
def test_http_errors_have_distinct_safe_categories(status_code, error_name, event_code):
    error = asyncio.run(capture_http_error(status_code))
    assert getattr(error, "error_code", None) == error_name
    assert error.code == event_code
    assert "private-provider" not in error.message
    assert "private-provider" not in str(error)


# 做什么：模拟输出部分文字后发生指定 HTTP 错误
# 入参：上游状态、历史和问题
# 返回：一个片段，随后抛分类异常
async def failing_model(status_code, history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "部分回答"}}]})
    raise await capture_http_error(status_code)


# 做什么：确认事件和历史保存同一错误分类，并释放生成占用
# 入参：隔离工厂、替换工具和分类参数
# 返回：无；状态、正文或错误码错误时失败
@pytest.mark.parametrize("status_code,error_name,event_code", HTTP_ERROR_CASES)
def test_error_category_survives_stream_and_history(chat_sessions, monkeypatch, caplog, status_code, error_name, event_code):
    monkeypatch.setattr(chat_stream, "stream_model", partial(failing_model, status_code))
    turn = new_turn(chat_sessions)
    with caplog.at_level(logging.INFO, logger=chat_stream.__name__):
        events = event_items(asyncio.run(collect_stream(chat_sessions, turn)))
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == event_code
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["error_code"] == error_name
    assert answer["status"] == "failed"
    assert answer["content"] == "部分回答"
    assert error_name in caplog.text
    assert "private-provider" not in caplog.text
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, turn.session_id).active_request_id is None


# 做什么：模拟建连阶段的传输异常
# 入参：异常类型和请求
# 返回：不返回，抛模拟异常
def failing_transport(error_type, request):
    raise error_type("private-provider-key-and-prompt", request=request)


# 做什么：确认网络超时与普通连接故障可区分
# 入参：异常类型和预期错误标签
# 返回：无；分类错误或泄露详情时失败
@pytest.mark.parametrize("error_type,error_name", [
    (httpx.ConnectTimeout, "model_timeout"), (httpx.ReadTimeout, "model_timeout"),
    (httpx.ConnectError, "model_connection_failed"), (httpx.RemoteProtocolError, "model_connection_failed"),
])
def test_transport_errors_are_classified(error_type, error_name):
    error = asyncio.run(capture_transport_error(error_type))
    assert error.error_code == error_name
    assert "private-provider" not in str(error)


# 做什么：通过模拟传输执行一次实际建连流程
# 入参：传输异常类型
# 返回：转换后的业务异常
async def capture_transport_error(error_type):
    transport = httpx.MockTransport(partial(failing_transport, error_type))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelUnavailableError) as captured:
            await send_attempt(client, {}, False)
    return captured.value


class BrokenResponseStream(httpx.AsyncByteStream):
    # 做什么：记录读取阶段要模拟的异常
    # 入参：HTTPX 异常类型
    # 返回：初始化后的模拟响应流
    def __init__(self, error_type):
        self.error_type = error_type

    # 做什么：先输出文字再模拟读取故障
    # 入参：无
    # 返回：一个合法 SSE 片段，之后抛网络异常
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise self.error_type("private-provider-key-and-prompt")


# 做什么：读取一条使用真实 HTTPX 迭代机制的模拟响应
# 入参：模拟 HTTP 传输
# 返回：业务异常与已收到片段
async def capture_response_error(transport):
    chunks = []
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelUnavailableError) as captured:
            async for chunk in response_events(client, {}):
                chunks.append(chunk)
    return captured.value, chunks


# 做什么：构造读取中断的响应并保留引用以检查关闭
# 入参：异常类型、响应记录和请求
# 返回：模拟流式响应
def broken_response(error_type, responses, request):
    response = httpx.Response(200, stream=BrokenResponseStream(error_type), request=request)
    responses.append(response)
    return response


# 做什么：确认输出后的读取错误同样分类且关闭上游
# 入参：网络异常类型和预期标签
# 返回：无；丢失部分文字或资源未关闭时失败
@pytest.mark.parametrize("error_type,error_name", [
    (httpx.ReadTimeout, "model_timeout"), (httpx.ReadError, "model_connection_failed"),
])
def test_stream_read_error_is_classified_and_closed(error_type, error_name, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_retries", 2)
    responses = []
    transport = httpx.MockTransport(partial(broken_response, error_type, responses))
    error, chunks = asyncio.run(capture_response_error(transport))
    assert error.error_code == error_name
    assert len(chunks) == 1
    assert chunks[0].choices[0].delta.content == "partial"
    assert responses[0].is_closed
    assert len(responses) == 1
    assert "private-provider" not in str(error)


# 做什么：确认无效内容、流内错误与连接截断归入协议异常
# 入参：模拟 SSE 文本
# 返回：无；错误分类错误或原始上游文案泄露时失败
@pytest.mark.parametrize("content", [
    'data: not-json\n\n', 'data: {"error":{"message":"private-provider-key-and-prompt"}}\n\n',
    'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
])
def test_protocol_errors_are_safe(content):
    transport = httpx.MockTransport(partial(text_response, content))
    error, chunks = asyncio.run(capture_response_error(transport))
    assert error.error_code == "model_protocol_error"
    assert "private-provider" not in str(error)


# 做什么：提供指定的成功 HTTP 响应正文
# 入参：SSE 内容与模拟请求
# 返回：内存响应
def text_response(content, request):
    return httpx.Response(200, text=content, request=request)


# 做什么：确认认证等永久错误不重试，原有瞬时错误只有限重试且保留分类
# 入参：配置替换工具、状态、预期次数和标签
# 返回：无；错误重试或最终分类丢失时失败
@pytest.mark.parametrize("status_code,attempt_count,error_name", [
    (401, 1, "model_authentication_failed"), (402, 1, "model_payment_required"),
    (403, 1, "model_permission_denied"), (400, 1, "model_request_rejected"),
    (429, 2, "model_rate_limited"), (503, 2, "model_unavailable"),
])
def test_retries_remain_bounded(monkeypatch, status_code, attempt_count, error_name):
    monkeypatch.setattr(get_settings(), "llm_max_retries", 1)
    monkeypatch.setattr(model_client, "RETRY_DELAY_SECONDS", 0)
    responses = []
    transport = httpx.MockTransport(partial(record_failure, responses, status_code))
    error = asyncio.run(capture_retry_error(transport))
    assert len(responses) == attempt_count
    assert error.error_code == error_name
    assert all(response.is_closed for response in responses)


# 做什么：记录失败响应以核对重试次数和资源释放
# 入参：响应列表、HTTP 状态和请求
# 返回：模拟失败响应
def record_failure(responses, status_code, request):
    response = provider_failure(status_code, request)
    responses.append(response)
    return response


# 做什么：执行包含重试策略的建连流程
# 入参：模拟 HTTP 传输
# 返回：重试完成后的最终业务异常
async def capture_retry_error(transport):
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ModelUnavailableError) as captured:
            await model_client.open_model_stream(client, {})
    return captured.value
