"""通过真正的本机 HTTP 连接验证断线，不依赖 TestClient 的响应缓冲。"""
import asyncio
import socket
from uuid import uuid4

import httpx
import uvicorn

from app.core.security import create_access_token
from app.db import session as database_session
from app.llm.client import ModelChunk
from app.main import app
from app.services import chat_stream
from app.services.chat_history import create_session
from tests.chat_support import chat_sessions, history_items


# 做什么：产生一个片段后等待，方便客户端在生成过程中关闭连接
# 入参：历史和问题
# 返回：模型片段流，取消时结束等待
async def slow_model(history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "已收到的部分回答"}}]})
    await asyncio.sleep(30)


# 做什么：等待真实服务器启动，失败时立即报错而不是无限等待
# 入参：Uvicorn 服务和启动任务
# 返回：无；超时或启动失败抛异常
async def wait_for_start(server, server_task):
    async with asyncio.timeout(5):
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("测试服务未成功启动")
            await asyncio.sleep(0.01)


# 做什么：读取到第一个增量帧后返回，让外层关闭实际 TCP 响应
# 入参：由调用方持有的响应行迭代器，避免函数返回时提前关闭连接
# 返回：无；没有收到增量时抛断言错误
async def read_first_delta(response_lines):
    async for line in response_lines:
        if line == "event: delta":
            return
    raise AssertionError("没有收到增量事件")


# 做什么：通过真实 HTTP 发问题，在首个增量后关闭连接
# 入参：本机服务地址和会话编号
# 返回：无；不调用外部模型
async def close_live_stream(base_url, session_id):
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    payload = {"request_id": str(uuid4()), "content": "验证关闭页面"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, trust_env=False) as client:
        async with client.stream("POST", f"/chat/sessions/{session_id}/messages", json=payload) as response:
            assert response.status_code == 200
            response_lines = response.aiter_lines()
            await read_first_delta(response_lines)
            state_response = await client.get(f"/chat/sessions/{session_id}/state")
            state = state_response.json()["data"]
            assert state["active_request_id"] == payload["request_id"]
            observed = await client.get(f"/chat/sessions/{session_id}/requests/{payload['request_id']}")
            assert observed.json()["data"]["answer"]["status"] == "generating"
            await response_lines.aclose()


# 做什么：等待断线收尾被保存到数据库
# 入参：会话工厂和会话编号
# 返回：中断后的助手消息；未收尾时超时报错
async def wait_for_interruption(sessions, session_id):
    async with asyncio.timeout(5):
        while True:
            messages = await asyncio.to_thread(history_items, sessions, session_id)
            if messages[-1]["status"] == "interrupted":
                return messages[-1]
            await asyncio.sleep(0.02)


# 做什么：启动随机端口服务，验证断线后收尾，并始终关闭测试服务器
# 入参：隔离会话工厂
# 返回：中断消息的正文
async def live_disconnect_check(sessions):
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    port = server_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", lifespan="on"))
    server_task = asyncio.create_task(server.serve(sockets=[server_socket]))
    try:
        await wait_for_start(server, server_task)
        chat_session = await asyncio.to_thread(create_session, sessions, 1)
        await close_live_stream(f"http://127.0.0.1:{port}", chat_session.id)
        answer = await wait_for_interruption(sessions, chat_session.id)
        return answer["content"]
    finally:
        server.should_exit = True
        await server_task
        server_socket.close()


# 做什么：证明真实 HTTP 断开会保存部分回答并标记中断
# 入参：内存库和模拟替换工具
# 返回：无；服务生命周期、鉴权或断线处理异常都会使测试失败
def test_real_http_disconnect_preserves_partial_answer(chat_sessions, monkeypatch):
    monkeypatch.setattr(database_session, "SessionLocal", chat_sessions)
    monkeypatch.setattr(chat_stream, "stream_model", slow_model)
    assert asyncio.run(live_disconnect_check(chat_sessions)) == "已收到的部分回答"

