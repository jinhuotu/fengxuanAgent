"""应用装配入口。

这个文件只做三件事：装配（中间件、异常处理器）、挂载（路由）、启停（lifespan）。
校验、查询、业务判断一律不写在这里——那些属于 services 和 api 层。
以后每加一组接口，这里只多一行 include_router。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionLocal
from app.utils.response import success

settings = get_settings()
# 先配日志再干别的：这样即使后面装配出错，报错也是带时间和模块名的
setup_logging(settings.app_env)
logger = get_logger(__name__)


# 做什么：启动和关闭时各打一行日志，方便在日志里分清"服务重启"和"请求失败"
# 入参：app 由 FastAPI 传入
# 返回：异步上下文管理器
# 为什么不在这里连库：库没起来时服务还应该能启动，让 /health 报出"库不通"，
# 比整个进程起不来更容易判断问题出在容器还是代码。
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("服务启动 | 环境=%s", settings.app_env)
    yield
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

# 认证路由挂在这里：以后每加一组接口，这里只多一行 include_router
app.include_router(auth_router)
# 用户管理路由：目前只有"管理员建用户"一个接口
app.include_router(users_router)


# 做什么：确认服务活着、数据库能连
# 入参：无
# 返回：统一响应；库不通时抛出异常，交给全局处理器转 500 并记日志
# 为什么每次都真查一次库：这个接口的用途就是探活，"服务在但库挂了"必须能被发现
@app.get("/health", summary="健康检查")
def health_check() -> dict:
    with SessionLocal() as db:
        db.execute(text("select 1"))
    return success({"status": "ok", "database": "ok"})
