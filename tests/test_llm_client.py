"""使用 HTTPX 模拟真实 SSE 协议，不依赖外网或 API Key。"""
import asyncio

import httpx
import pytest

from app.core.chat_errors import ModelUnavailableError
from app.llm.client import parse_chunk, read_model_chunks


# 做什么：用真实 httpx 响应迭代供应商 SSE 文本
# 入参：供应商响应正文
# 返回：校验后的片段数组；无结束标记或无效内容会抛异常
async def read_provider_content(content):
    response = httpx.Response(200, text=content, request=httpx.Request("POST", "https://example.invalid"))
    chunks = []
    async for chunk in read_model_chunks(response):
        chunks.append(chunk)
    return chunks


# 做什么：确认正确处理心跳注释、文本片段和完成标记
# 入参：无
# 返回：无；不能把注释和结束标记当成回答文字
def test_provider_sse_parses_comments_and_done_marker():
    content = ': keepalive\n\ndata: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    chunks = asyncio.run(read_provider_content(content))
    assert len(chunks) == 1
    assert chunks[0].choices[0].delta.content == "你好"


# 做什么：确认缺少结束标记的连接被识别为截断
# 入参：无
# 返回：无；不能静默返回成功
def test_provider_eof_without_done_is_rejected():
    content = 'data: {"choices":[{"delta":{"content":"部分"}}]}\n\n'
    with pytest.raises(ModelUnavailableError):
        asyncio.run(read_provider_content(content))


# 做什么：确认无效供应商结构不会穿透客户端
# 入参：无效 JSON 或错误字段
# 返回：无；必须转成统一安全异常
@pytest.mark.parametrize("content", ["not-json", '{"choices":[{"delta":{"content":12}}]}', '{"error":{"message":"private detail"}}'])
def test_invalid_provider_chunks_are_rejected(content):
    with pytest.raises(ModelUnavailableError):
        parse_chunk(content)

