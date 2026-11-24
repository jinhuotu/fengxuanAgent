"""用户管理接口。

按权限创建用户，管理员给用户追加角色。
登录、取当前用户仍在 auth.py —— 一个是"进门的门禁"，一个是"进来以后管事"，不混在一起。
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_user_creation_permission, require_user_list_permission
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.rbac import RoleBindRequest
from app.schemas.user import UserCreate, UserListQuery
from app.services import rbac as rbac_service
from app.services import user as user_service
from app.utils.response import success

router = APIRouter(prefix="/users", tags=["用户管理"])


# 做什么：接收分页参数并返回公开用户列表
# 入参：已校验的页码、每页条数和数据库会话
# 返回：统一分页响应；无权限或参数不合法时返回对应错误
@router.get("", summary="用户列表（需要 users:list 权限）", dependencies=[Depends(require_user_list_permission)])
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
@router.post("", summary="新建用户（需要 users:create 权限）")
def create_user(
    payload: UserCreate,
    db_session: Session = Depends(get_db),
    current_user: UserInfo = Depends(require_user_creation_permission),
) -> dict:
    created_user = user_service.create_user(db_session, payload)
    return success(created_user.model_dump())


# 做什么：管理员给指定用户追加一个角色
# 入参：角色标识、目标用户编号和独立写入会话
# 返回：统一响应，created 表示是否新增；无权或目标不存在时返回业务错误
@router.post("/{user_id}/roles", summary="追加用户角色（仅管理员）", dependencies=[Depends(get_current_admin)])
def bind_user_role(
    payload: RoleBindRequest,
    user_id: int = Path(gt=0),
    # 新手注意：鉴权用一张草稿纸，写入另取一张，避免查询已打开事务。
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    created_binding = rbac_service.bind_user_role(db_session, user_id, payload.role_code)
    return success({"created": created_binding})
