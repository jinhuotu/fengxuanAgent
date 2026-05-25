"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logger import setup_logging

settings = get_settings()
setup_logging(settings.app_debug)
logger = logging.getLogger("agent-backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys

    # 启动时打印 MCP 路由，便于快速发现进程或端口配置错误。
    mcp_paths = sorted(
        {
            getattr(route, "path", "")
            for route in app.router.routes
            if "/api/v1/mcp" in getattr(route, "path", "")
        }
    )
    logger.info("RouteProbe: mcp_routes_count=%s mcp_routes=%s", len(mcp_paths), mcp_paths)

    # 与「终端 pip list 有 mcp、但 capabilities 仍报缺包」对齐：此处打印实际跑后端的解释器。
    try:
        from mcp.client.session import ClientSession  # noqa: F401

        logger.info("MCP SDK probe: ok python=%s", sys.executable)
    except BaseException as e:
        logger.warning(
            "MCP SDK probe: import failed python=%s err=%r type=%s — "
            "请在该解释器环境中安装 mcp（例如 poetry install 或 pip install \"mcp>=1.8.0,<2.0.0\"）。",
            sys.executable,
            e,
            type(e).__name__,
        )

    yield

    # 尽力优雅关闭已启动的 MCP 子进程。
    try:
        from app.services.mcp_runtime_service import get_mcp_runtime_service

        await get_mcp_runtime_service().shutdown()
    except Exception:
        # 关闭失败不应导致服务进程崩溃。
        pass


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router)


def _app_package_version() -> str:
    try:
        return version("fengxuan-agent-backend")
    except PackageNotFoundError:
        return "dev"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": _app_package_version()}
