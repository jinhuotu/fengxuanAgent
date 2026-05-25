"""根 API 路由聚合。"""

from fastapi import APIRouter
from app.api.v1 import agent_tools, auth, chat, models, prompts, sessions, knowledge_bases, system, mcp

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
api_router.include_router(knowledge_bases.router, prefix="/knowledge-bases", tags=["knowledge-bases"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(mcp.router, prefix="/mcp", tags=["mcp"])
api_router.include_router(agent_tools.router, prefix="/agent-tools", tags=["agent-tools"])
