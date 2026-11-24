"""直接调用兼容 Chat Completions 的流式 HTTP 接口。"""
import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from http import HTTPStatus

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.chat_errors import (
    ModelUnavailableError, ModelAuthenticationError, ModelPermissionError, ModelRateLimitError,
    ModelPaymentError, ModelRequestError, ModelTimeoutError, ModelConnectionError, ModelProtocolError,
)
from app.core.config import get_settings
from app.schemas.chat import ContextMessage
from app.llm.tokens import ensure_request_budget

CHAT_COMPLETIONS_PATH = "/chat/completions"
SSE_DATA_PREFIX = "data:"
SSE_DONE = "[DONE]"
RETRY_DELAY_SECONDS = 0.5
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
SUPPORTED_FINISH_REASONS = {"stop", "length"}
HTTP_MODEL_ERRORS = {
    HTTPStatus.UNAUTHORIZED: ModelAuthenticationError,
    HTTPStatus.FORBIDDEN: ModelPermissionError,
    HTTPStatus.PAYMENT_REQUIRED: ModelPaymentError,
    HTTPStatus.TOO_MANY_REQUESTS: ModelRateLimitError,
    HTTPStatus.REQUEST_TIMEOUT: ModelTimeoutError,
    HTTPStatus.GATEWAY_TIMEOUT: ModelTimeoutError,
}
SYSTEM_PROMPT = Path(__file__).with_name("prompts").joinpath("chat.txt").read_text(encoding="utf-8").strip()


class ModelDelta(BaseModel):
    content: str | None = None


class ModelChoice(BaseModel):
    delta: ModelDelta = Field(default_factory=ModelDelta)
    finish_reason: str | None = None


class ModelUsage(BaseModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class ModelChunk(BaseModel):
    choices: list[ModelChoice] = Field(default_factory=list)
    usage: ModelUsage | None = None


# 做什么：组装供应商请求，不允许前端覆盖系统提示词或模型配置
# 入参：近期成功历史与当前问题
# 返回：包含流式选项的请求体
def model_payload(history: list[ContextMessage], question: str) -> dict:
    settings = get_settings()
    ensure_request_budget(SYSTEM_PROMPT, history, question)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in history:
        messages.append(message.model_dump())
    messages.append({"role": "user", "content": question})
    return {"model": settings.llm_model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True}, "max_tokens": settings.llm_max_output_tokens,
            "reasoning_effort": "none"}


# 做什么：执行一次建连，只在尚未输出内容时允许有限重试
# 入参：HTTP 客户端、请求体和是否还可以重试
# 返回：成功的流式响应；可重试故障返回 None，其他错误抛安全异常
async def send_attempt(client: httpx.AsyncClient, payload: dict, retry_allowed: bool) -> httpx.Response | None:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}", "Accept": "text/event-stream"}
    request = client.build_request("POST", settings.llm_base_url.rstrip("/") + CHAT_COMPLETIONS_PATH,
                                   headers=headers, json=payload)
    try:
        response = await client.send(request, stream=True)
    except httpx.TransportError as error:
        if retry_allowed:
            return None
        raise transport_error(error) from error
    if response.is_success:
        return response
    await response.aclose()
    if retry_allowed and response.status_code in RETRYABLE_HTTP_STATUSES:
        return None
    raise http_model_error(response.status_code)


# 做什么：将上游 HTTP 状态转换成固定安全分类，不读取错误正文
# 入参：上游 HTTP 状态码
# 返回：模型业务异常；未知服务错误归入暂不可用
def http_model_error(status_code: int) -> ModelUnavailableError:
    error_type = HTTP_MODEL_ERRORS.get(status_code)
    if error_type is not None:
        return error_type()
    if HTTPStatus.BAD_REQUEST <= status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
        return ModelRequestError()
    return ModelUnavailableError()


# 做什么：区分网络超时和其他传输异常，不暴露地址与原始异常
# 入参：HTTPX 传输异常
# 返回：固定文案和分类的模型业务异常
def transport_error(error: httpx.TransportError) -> ModelUnavailableError:
    if isinstance(error, httpx.TimeoutException):
        return ModelTimeoutError()
    return ModelConnectionError()


# 做什么：按配置次数尝试建立模型连接，不重播已经收到的片段
# 入参：HTTP 客户端和请求体
# 返回：可读取的响应；重试耗尽抛业务异常
async def open_model_stream(client: httpx.AsyncClient, payload: dict) -> httpx.Response:
    maximum_retries = get_settings().llm_max_retries
    for attempt in range(maximum_retries + 1):
        response = await send_attempt(client, payload, attempt < maximum_retries)
        if response is not None:
            return response
        await asyncio.sleep(RETRY_DELAY_SECONDS)
    raise ModelUnavailableError()


# 做什么：校验供应商的单条 SSE 内容
# 入参：data 字段中的 JSON 文本
# 返回：结构合法的模型片段；无效或异常返回统一模型错误
def parse_chunk(content: str) -> ModelChunk:
    try:
        chunk = ModelChunk.model_validate_json(content)
    except ValidationError as error:
        raise ModelProtocolError() from error
    if not chunk.choices and chunk.usage is None:
        raise ModelProtocolError()
    return chunk


# 做什么：读取 SSE，要求明确结束标记，避免把网络截断当成功
# 入参：已连接的模型响应
# 返回：逐个校验后的片段；截断、未知结束类型或超长单片段抛模型异常
async def read_model_chunks(response: httpx.Response) -> AsyncIterator[ModelChunk]:
    async for line in response.aiter_lines():
        if not line.startswith(SSE_DATA_PREFIX):
            continue
        content = line[len(SSE_DATA_PREFIX):].strip()
        if content == SSE_DONE:
            return
        if len(content) > get_settings().chat_max_answer_characters:
            raise ModelUnavailableError("模型单次返回内容过长")
        yield parse_chunk(content)
    raise ModelProtocolError()


# 新手注意：异步片段流像分批送菜，拿到一段就交给页面，不等整份回答完成。
# 做什么：管理模型连接生命周期，取消请求时及时关闭上游连接
# 入参：近期成功历史和当前问题
# 返回：异步片段流；网络故障转为安全业务异常
async def stream_model(history: list[ContextMessage], question: str) -> AsyncIterator[ModelChunk]:
    settings = get_settings()
    async with (
        httpx.AsyncClient(timeout=settings.llm_timeout_seconds, trust_env=False) as client,
        aclosing(response_events(client, model_payload(history, question))) as events,
    ):
        async for chunk in events:
            yield chunk


# 做什么：读取模型响应并把网络异常转成安全错误，结束时关闭响应
# 入参：客户端和模型请求体
# 返回：经过校验的供应商片段流；失败抛业务异常
async def response_events(client: httpx.AsyncClient, payload: dict) -> AsyncIterator[ModelChunk]:
    response = await open_model_stream(client, payload)
    events = read_model_chunks(response)
    try:
        async for chunk in events:
            yield chunk
    except httpx.TransportError as error:
        raise transport_error(error) from error
    finally:
        await events.aclose()
        await response.aclose()

