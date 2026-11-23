"""用户管理接口。

目前只有一个动作：管理员新建用户。
登录、取当前用户仍在 auth.py —— 一个是"进门的门禁"，一个是"进来以后管事"，不混在一起。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.user import UserCreate
from app.services import user as user_service
from app.utils.response import success

router = APIRouter(prefix="/users", tags=["用户管理"])


# 做什么：管理员新建一个普通账号
# 入参：payload 新用户信息；current_admin 只用于鉴权，函数体里不读它的值
# 返回：统一响应，data 是新用户信息（不含密码哈希）
# 失败：非管理员 -> 403/2001；用户名重复 -> 409/1005；字段不合法 -> 422/1000
@router.post("", summary="新建用户（仅管理员）")
def create_user(
    payload: UserCreate,
    db_session: Session = Depends(get_db),
    current_admin: UserInfo = Depends(get_current_admin),
) -> dict:
    created_user = user_service.create_user(db_session, payload)
    return success(created_user.model_dump())
