"""Agent 工具目录（内置 + MCP 元数据）及内置工具开关偏好。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.agent_tools import UserBuiltinToolPref
from app.models.mcp import McpServerConfig, McpToolMetadata
from app.services.mcp_server_service import list_mcp_servers, mcp_server_to_dict
from app.services.tools.builtin_registry import (
    ALLOWED_TOOL_KEYS,
    BUILTIN_TOOL_SPECS,
    list_builtin_specs,
)

logger = logging.getLogger(__name__)


def _is_missing_user_builtin_tool_prefs_table(exc: BaseException) -> bool:
    """数据库尚未执行迁移（Alembic 修订 20260509_0003）时为 True。"""
    seen: list[BaseException] = [exc]
    orig = getattr(exc, "orig", None)
    if orig is not None and orig is not exc:
        seen.append(orig)
    for e in seen:
        msg = str(e).lower()
        if "user_builtin_tool_prefs" not in msg:
            continue
        if (
            "doesn't exist" in msg
            or "does not exist" in msg
            or "no such table" in msg
            or "unknown table" in msg
        ):
            return True
        args = getattr(e, "args", ())
        if args and args[0] == 1146:
            return True
    return False


def get_effective_builtin_flags(db: Session, user_id: int) -> dict[str, bool]:
    """tool_key -> 是否启用（无记录时默认为 True）。"""
    keys = [s.tool_key for s in BUILTIN_TOOL_SPECS]
    try:
        rows = (
            db.query(UserBuiltinToolPref)
            .filter(UserBuiltinToolPref.user_id == user_id, UserBuiltinToolPref.tool_key.in_(keys))
            .all()
        )
    except SQLAlchemyError as e:
        if _is_missing_user_builtin_tool_prefs_table(e):
            db.rollback()
            logger.warning(
                "表 user_builtin_tool_prefs 不存在，内置工具按全部启用返回。请在项目根目录执行: alembic upgrade head"
            )
            return {k: True for k in keys}
        raise
    by_key = {r.tool_key: bool(r.enabled) for r in rows}
    return {k: by_key.get(k, True) for k in keys}


def set_builtin_tool_pref(db: Session, user_id: int, tool_key: str, enabled: bool) -> UserBuiltinToolPref:
    if tool_key not in ALLOWED_TOOL_KEYS:
        raise ValueError(f"Unknown tool_key: {tool_key}")
    try:
        row = (
            db.query(UserBuiltinToolPref)
            .filter(UserBuiltinToolPref.user_id == user_id, UserBuiltinToolPref.tool_key == tool_key)
            .first()
        )
        if row is None:
            row = UserBuiltinToolPref(user_id=user_id, tool_key=tool_key, enabled=enabled)
            db.add(row)
        else:
            row.enabled = enabled
        db.commit()
        db.refresh(row)
        return row
    except SQLAlchemyError as e:
        db.rollback()
        if _is_missing_user_builtin_tool_prefs_table(e):
            raise HTTPException(
                status_code=503,
                detail="数据库缺少表 user_builtin_tool_prefs，无法保存开关。请在项目根目录执行: alembic upgrade head",
            ) from e
        raise


def _mcp_tool_counts(db: Session, user_id: int) -> dict[int, int]:
    """server_config_id -> 已缓存的工具数量。"""
    rows = (
        db.query(McpToolMetadata.server_config_id, func.count(McpToolMetadata.id))
        .join(McpServerConfig, McpServerConfig.id == McpToolMetadata.server_config_id)
        .filter(McpServerConfig.user_id == user_id)
        .group_by(McpToolMetadata.server_config_id)
        .all()
    )
    return {int(sid): int(cnt) for sid, cnt in rows}


def build_agent_tools_catalog(db: Session, user_id: int) -> dict[str, Any]:
    """工具扩展页所需的统一数据结构。"""
    flags = get_effective_builtin_flags(db, user_id)
    builtins: list[dict[str, Any]] = []
    for spec in list_builtin_specs():
        builtins.append(
            {
                "kind": "builtin",
                "tool_key": spec.tool_key,
                "name": spec.display_name,
                "description": spec.description,
                "requires_kb": spec.requires_kb,
                "enabled": flags[spec.tool_key],
            }
        )

    counts = _mcp_tool_counts(db, user_id)
    mcp_rows = list_mcp_servers(db, user_id)
    mcp_servers: list[dict[str, Any]] = []
    for row in mcp_rows:
        d = mcp_server_to_dict(row)
        d["kind"] = "mcp"
        d["tool_count"] = counts.get(row.id, 0)
        mcp_servers.append(d)

    return {
        "builtins": builtins,
        "mcp_servers": mcp_servers,
    }
