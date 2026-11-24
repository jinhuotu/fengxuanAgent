"""唯一的 SSE 编码工具，普通 JSON 响应不使用这个封装。"""
import json

from anyio import CancelScope
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send
from starlette.background import BackgroundTask


# 做什么：把事件名称和公开内容编码为完整 SSE 帧
# 入参：事件名称和可以 JSON 序列化的字典
# 返回：包含空行终止符的 UTF-8 文本；序列化错误直接抛出
def encode_event(event_name: str, payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_name}\ndata: {serialized}\n\n"


class ClosingStreamingResponse(StreamingResponse):
    """发送失败时也显式关闭生成器，触发服务中的中断持久化。"""

    # 做什么：响应结束或客户端断开时关闭异步生成器
    # 入参：ASGI 请求环境、接收函数和发送函数
    # 返回：无；保留原传输异常，同时执行生成器清理
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        cleanup = self.background
        self.background = None
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await self.close_resources(cleanup)

    # 做什么：即使响应头发送失败，也执行中断回调收尾已接收的请求
    # 入参：响应收尾回调，只运行一次
    # 返回：无；不吞持久化异常
    async def close_resources(self, cleanup: BackgroundTask | None) -> None:
        try:
            await self.body_iterator.aclose()
        finally:
            if cleanup is not None:
                await cleanup()


