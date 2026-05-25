"""MCP 扩展 API（能力发现 + Server CRUD）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.mcp import McpServerCreateRequest, McpServerUpdateRequest
from app.schemas.response import ok
from app.services.mcp_gateway_service import McpGatewayService
from app.services.mcp_runtime_service import get_mcp_runtime_service
from app.services.mcp_server_service import (
    create_mcp_server,
    get_mcp_server,
    list_mcp_servers,
    mcp_server_to_dict,
    update_mcp_server,
)

router = APIRouter()


@router.get("/capabilities")
async def capabilities(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Return current user's enabled MCP servers + their tools list.
    """
    gateway = McpGatewayService(db)
    data = await gateway.get_capabilities(current_user.id)
    return ok(data)


@router.get("/servers")
def list_servers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """列出当前用户全部 MCP Server 配置（含已禁用）。"""
    rows = list_mcp_servers(db, current_user.id)
    return ok([mcp_server_to_dict(r) for r in rows])


@router.post("/servers")
def create_server(
    payload: McpServerCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建 MCP Server 配置。"""
    row = create_mcp_server(db, current_user.id, payload)
    return ok(mcp_server_to_dict(row))


@router.put("/servers/{server_pk}")
async def update_server(
    server_pk: int,
    payload: McpServerUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新 MCP Server 配置；若存在则关闭当前 stdio 会话。"""
    row = update_mcp_server(db, current_user.id, server_pk, payload)
    await get_mcp_runtime_service().drop_handle(row.id)
    return ok(mcp_server_to_dict(row))


@router.delete("/servers/{server_pk}")
async def delete_server(
    server_pk: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除 MCP Server 配置。"""
    row = get_mcp_server(db, current_user.id, server_pk)
    if not row:
        raise HTTPException(status_code=404, detail="MCP 配置不存在")
    await get_mcp_runtime_service().drop_handle(server_pk)
    db.delete(row)
    db.commit()
    return ok(message="deleted")
