"""MCP Server 配置持久化（MySQL，按用户隔离）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.mcp import McpServerConfig
from app.schemas.mcp import McpServerCreateRequest, McpServerUpdateRequest


def mcp_server_to_dict(row: McpServerConfig) -> dict:
    return {
        "id": row.id,
        "server_id": row.server_id,
        "name": row.name,
        "command": row.command,
        "args_json": row.args_json or [],
        "env_json": row.env_json or {},
        "working_dir": row.working_dir,
        "enabled": bool(row.enabled),
        "startup_timeout_seconds": row.startup_timeout_seconds,
        "restart_policy": row.restart_policy,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_mcp_servers(db: Session, user_id: int) -> list[McpServerConfig]:
    return (
        db.query(McpServerConfig)
        .filter(McpServerConfig.user_id == user_id)
        .order_by(McpServerConfig.id.asc())
        .all()
    )


def get_mcp_server(db: Session, user_id: int, pk: int) -> McpServerConfig | None:
    row = db.get(McpServerConfig, pk)
    if not row or row.user_id != user_id:
        return None
    return row


def create_mcp_server(db: Session, user_id: int, payload: McpServerCreateRequest) -> McpServerConfig:
    dup = (
        db.query(McpServerConfig)
        .filter(McpServerConfig.user_id == user_id, McpServerConfig.server_id == payload.server_id)
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail=f"MCP server_id 已存在: {payload.server_id}")

    row = McpServerConfig(
        user_id=user_id,
        server_id=payload.server_id.strip(),
        name=payload.name.strip(),
        command=payload.command.strip(),
        args_json=payload.args_json or [],
        env_json=payload.env_json or {},
        working_dir=payload.working_dir.strip() if payload.working_dir else None,
        enabled=payload.enabled,
        startup_timeout_seconds=payload.startup_timeout_seconds,
        restart_policy=payload.restart_policy,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_mcp_server(db: Session, user_id: int, pk: int, payload: McpServerUpdateRequest) -> McpServerConfig:
    row = get_mcp_server(db, user_id, pk)
    if not row:
        raise HTTPException(status_code=404, detail="MCP 配置不存在")

    data = payload.model_dump(exclude_none=True)
    if "server_id" in data and data["server_id"] != row.server_id:
        dup = (
            db.query(McpServerConfig)
            .filter(
                McpServerConfig.user_id == user_id,
                McpServerConfig.server_id == data["server_id"],
                McpServerConfig.id != pk,
            )
            .first()
        )
        if dup:
            raise HTTPException(status_code=409, detail=f"MCP server_id 已存在: {data['server_id']}")
        row.server_id = data["server_id"].strip()
    if "name" in data:
        row.name = data["name"].strip()
    if "command" in data:
        row.command = data["command"].strip()
    if "args_json" in data:
        row.args_json = data["args_json"] or []
    if "env_json" in data:
        row.env_json = data["env_json"] or {}
    if "working_dir" in data:
        row.working_dir = data["working_dir"].strip() if data["working_dir"] else None
    if "enabled" in data:
        row.enabled = data["enabled"]
    if "startup_timeout_seconds" in data:
        row.startup_timeout_seconds = data["startup_timeout_seconds"]
    if "restart_policy" in data:
        row.restart_policy = data["restart_policy"]

    db.commit()
    db.refresh(row)
    return row


def delete_mcp_server(db: Session, user_id: int, pk: int) -> None:
    row = get_mcp_server(db, user_id, pk)
    if not row:
        raise HTTPException(status_code=404, detail="MCP 配置不存在")
    db.delete(row)
    db.commit()
