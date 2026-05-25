"""Agent 工具扩展：目录与内置开关。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.agent_tools import BuiltinToolPrefUpdateRequest
from app.schemas.response import ok
from app.services.agent_tool_service import build_agent_tools_catalog, set_builtin_tool_pref
from app.services.tools.builtin_registry import ALLOWED_TOOL_KEYS

router = APIRouter()


@router.get("/catalog")
def get_tool_extensions_catalog(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """工具扩展页：内置工具 + MCP Server 列表。"""
    data = build_agent_tools_catalog(db, current_user.id)
    return ok(data)


@router.patch("/builtin/{tool_key}")
def patch_builtin_tool_pref(
    tool_key: str,
    payload: BuiltinToolPrefUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if tool_key not in ALLOWED_TOOL_KEYS:
        raise HTTPException(status_code=400, detail="未知的 tool_key")
    row = set_builtin_tool_pref(db, current_user.id, tool_key, payload.enabled)
    return ok({"tool_key": row.tool_key, "enabled": bool(row.enabled)})
