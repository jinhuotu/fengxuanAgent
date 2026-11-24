"""用户管理接口。

按权限创建用户、查看、追加和撤销角色。
登录、取当前用户仍在 auth.py —— 一个是"进门的门禁"，一个是"进来以后管事"，不混在一起。
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.deps import (
    require_role_assign_permission,
    require_user_creation_permission,
    require_user_disable_permission,
    require_user_list_permission,
)
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.rbac import MAX_ROLE_CODE_LENGTH, RoleBindRequest
from app.schemas.user import UserCreate, UserListQuery, UserStatusChangeRequest
from app.services import rbac as rbac_service
from app.services import user as user_service
from app.utils.response import success

router = APIRouter(tags=["用户管理"])


# 做什么：接收分页参数并返回公开用户列表
# 入参：已校验的页码、每页条数和数据库会话
# 返回：统一分页响应；无权限或参数不合法时返回对应错误
@router.get(
    "/users", summary="用户列表（需要 users:list 权限）",
    dependencies=[Depends(require_user_list_permission)],
)
def list_users(
    query: UserListQuery = Depends(),
    db_session: Session = Depends(get_db),
) -> dict:
    user_page = user_service.list_users(db_session, query)
    return success(user_page.model_dump())


# 做什么：持有 users:create 权限的用户新建一个普通账号
# 入参：payload 新用户信息；current_user 只用于鉴权，函数体里不读它的值
# 返回：统一响应，data 是新用户信息（不含密码哈希）
# 失败：无创建权限 -> 403/2001；用户名重复 -> 409/1005；字段不合法 -> 422/1000
@router.post("/users", summary="新建用户（需要 users:create 权限）")
def create_user(
    payload: UserCreate,
    db_session: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_user_creation_permission),
) -> dict:
    created_user = user_service.create_user(db_session, payload)
    return success(created_user.model_dump())


# 做什么：接收停用或启用请求并转交用户服务
# 入参：目标用户编号、已校验状态、已授权操作者和独立写入会话
# 返回：统一响应中的 changed 和 status；失败时返回对应业务错误
@router.patch("/users/{user_id}/status", summary="切换用户状态（需要 users:disable 权限）")
def change_user_status(
    payload: UserStatusChangeRequest,
    user_id: int = Path(gt=0),
    current_user: UserInfo = Depends(require_user_disable_permission),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    changed = user_service.change_user_status(
        db_session, operator_id=current_user.id, user_id=user_id, status=payload.status
    )
    return success({"changed": changed, "status": payload.status})


# 做什么：给他人追加一个角色
# 入参：角色标识、目标用户编号、已获授权的操作者和独立写入会话
# 返回：统一响应，created 表示是否新增；失败时返回对应业务错误
@router.post("/users/{user_id}/roles", summary="追加用户角色（需要 users:assign_role 权限）")
def bind_user_role(
    payload: RoleBindRequest,
    user_id: int = Path(gt=0),
    current_user: UserInfo = Depends(require_role_assign_permission),
    # 新手注意：鉴权用一张草稿纸，写入另取一张，避免查询已打开事务。
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    created_binding = rbac_service.bind_user_role(
        db_session, user_id, payload.role_code, operator_id=current_user.id
    )
    return success({"created": created_binding})


# 做什么：撤销其他用户的指定角色绑定
# 入参：目标用户编号、角色代码、已授权操作者和独立写入会话
# 返回：统一响应，removed 表示是否撤销；失败时返回对应业务错误
@router.delete(
    "/users/{user_id}/roles/{role_code}", summary="撤销用户角色（需要 users:assign_role 权限）"
)
def unbind_user_role(
    user_id: int = Path(gt=0),
    role_code: str = Path(min_length=1, max_length=MAX_ROLE_CODE_LENGTH),
    current_user: UserInfo = Depends(require_role_assign_permission),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    removed_binding = rbac_service.unbind_user_role(
        db_session, operator_id=current_user.id, user_id=user_id, role_code=role_code
    )
    return success({"removed": removed_binding})


# 做什么：返回角色清单，只包含角色代码和显示名称
# 入参：通过 users:list 鉴权后的请求级数据库会话
# 返回：统一响应的 items；无权限时返回对应业务错误
@router.get(
    "/roles", summary="角色清单（需要 users:list 权限）",
    dependencies=[Depends(require_user_list_permission)],
)
def list_roles(db_session: Session = Depends(get_db)) -> dict:
    roles = rbac_service.list_roles(db_session)
    role_items = [role.model_dump() for role in roles]
    return success({"items": role_items})
