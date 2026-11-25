"""应用装配入口。

这个文件只做三件事：装配（中间件、异常处理器）、挂载（路由）、启停（lifespan）。
校验、查询、业务判断一律不写在这里——那些属于 services 和 api 层。
以后每加一组接口，这里只多一行 include_router。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.api.v1.chat import router as chat_router
from app.api.v1.knowledge import chunk_router as knowledge_chunk_router
from app.api.v1.knowledge import router as knowledge_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_request_id, get_logger, release_request_id, setup_logging
from app.db.session import SessionLocal
from app.db import session as database_session
from app.services.chat_recovery import recovery_lifespan
from app.services.chat_prewarm import prewarm_lifespan
from app.utils.response import success

settings = get_settings()
# 先配日志再干别的：这样即使后面装配出错，报错也是带时间和模块名的
setup_logging(settings.app_env)
logger = get_logger(__name__)


# 做什么：记录启停并托管过期生成恢复任务
# 入参：app 由 FastAPI 传入
# 返回：异步上下文管理器
# 恢复扫描在后台连接数据库，故障不阻塞启动，由 /health 继续报告数据库状态。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("服务启动 | 环境=%s", settings.app_env)
    async with recovery_lifespan(database_session.SessionLocal), prewarm_lifespan() as prewarm_queue:
        app.state.chat_prewarm_queue = prewarm_queue
        try:
            yield
        finally:
            app.state.chat_prewarm_queue = None
    logger.info("服务关闭")


app = FastAPI(title="锋煊智能体 API", version="0.1.0", lifespan=lifespan)

# 允许的来源从 .env 读，绝不写 "*"：允许所有来源等于没有跨域保护
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 三个全局异常处理器在这里挂上，接口里就不用各自 try/except
register_exception_handlers(app)


# 做什么：给每个请求分配编号、写入响应头，并记录一条访问日志
# 入参：请求与后续处理链
# 返回：响应；未带 X-Request-ID 时自动生成，带上后前端报障可直接给这个号
async def log_requests(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]
    token = bind_request_id(request_id)
    started_at = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 兜底日志：即使异常处理器没接住，也要留下可定位的一行与堆栈
        logger.exception("请求异常 | %s %s | elapsed_ms=%.1f", request.method, request.url.path,
                         (perf_counter() - started_at) * 1000)
        release_request_id(token)
        raise
    response.headers["X-Request-ID"] = request_id
    # 流式回答在返回响应时正文还没开始发，这一行只代表握手耗时，正文时长看"模型调用结束"。
    is_stream = response.headers.get("content-type", "").startswith("text/event-stream")
    logger.info("请求完成 | %s %s | status=%s | elapsed_ms=%.1f | user_id=%s | stream=%s",
                request.method, request.url.path, response.status_code,
                (perf_counter() - started_at) * 1000, getattr(request.state, "user_id", "-"), is_stream)
    release_request_id(token)
    return response


app.middleware("http")(log_requests)

# 认证路由挂在这里：以后每加一组接口，这里只多一行 include_router
app.include_router(auth_router)
# 用户管理路由：目前只有"管理员建用户"一个接口
app.include_router(users_router)
app.include_router(chat_router)
app.include_router(knowledge_router)
# 对话引用只带切片编号，独立路由可直接查原文。
app.include_router(knowledge_chunk_router)


# 做什么：确认服务活着、数据库能连
# 入参：无
# 返回：统一响应；库不通时抛出异常，交给全局处理器转 500 并记日志
# 为什么每次都真查一次库：这个接口的用途就是探活，"服务在但库挂了"必须能被发现
@app.get("/health", summary="健康检查")
def health_check() -> dict:
    with SessionLocal() as db:
        db.execute(text("select 1"))
    return success({"status": "ok", "database": "ok"})
