"""认证接口。

这一层只做三件事：收参数、调 service、包统一响应。
判断账号对不对、密码错没错，一律在 services 里，这里不写一个 if。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_access_token, get_current_user
from app.db.session import get_db
from app.schemas.auth import ChangePasswordRequest, LoginRequest, UserInfo
from app.services import auth as auth_service
from app.services import password as password_service
from app.services import rbac as rbac_service
from app.utils.response import success

# 前缀 /auth 与前端 src/api/auth.ts 的 '/auth/login' 对上；
# 前端请求 /api/auth/login，Vite 代理把 /api 去掉后正好落到这里
router = APIRouter(prefix="/auth", tags=["认证"])


# 做什么：登录接口入口，把请求体交给 service，再把结果包成统一响应
# 入参：payload 已通过 Pydantic 校验的请求体；db_session 请求级会话，由 get_db 注入
# 返回：{code, message, data}，data 为 access_token / token_type / expires_in
# 失败：service 抛出的业务异常交给全局处理器转 401 或 403，本函数不捕获
@router.post("/login", summary="登录")
def login_endpoint(payload: LoginRequest, db_session: Session = Depends(get_db)) -> dict:
    login_result = auth_service.login(
        db_session, username=payload.username, password=payload.password
    )
    return success(login_result.model_dump())


# 做什么：返回当前登录用户的信息，证明这枚 token 确实代表某个人
# 入参：current_user 由 get_current_user 依赖解析出来，没带 token 到不了这里
# 返回：统一响应，data 为 id / username / display_name / status / created_at
# 失败：token 缺失、过期、伪造，由依赖抛异常转 401；本函数不做任何判断
@router.get("/me", summary="当前用户")
def me_endpoint(current_user: UserInfo = Depends(get_current_user)) -> dict:
    return success(current_user.model_dump())


# 做什么：返回当前登录用户的权限清单
# 入参：由凭证确认的当前用户和请求级数据库会话
# 返回：统一权限响应；未登录或账号停用由身份依赖拒绝
@router.get("/me/permissions", summary="当前用户权限清单")
def my_permissions_endpoint(
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    permission_codes = rbac_service.list_permission_codes(db_session, current_user.id)
    return success({"permissions": permission_codes})


# 做什么：接收本人改密请求，将凭证与密码交给服务核验保存
# 入参：改密表单、Bearer token 和尚未开启事务的会话
# 返回：统一成功响应；业务失败交给全局处理器返回对应错误
@router.post("/change-password", summary="修改本人密码")
def change_password_endpoint(
    payload: ChangePasswordRequest,
    token: str = Depends(get_access_token),
    db_session: Session = Depends(get_db),
) -> dict:
    password_service.change_password(
        db_session, token, payload.current_password, payload.new_password
    )
    return success(message="密码修改成功，请重新登录")


# 做什么：接收退出请求并撤销本人已签发的登录凭证
# 入参：Bearer token 和尚未开启事务的会话
# 返回：统一成功响应；凭证无效时返回对应业务错误
@router.post("/logout", summary="退出登录")
def logout_endpoint(
    token: str = Depends(get_access_token),
    db_session: Session = Depends(get_db),
) -> dict:
    auth_service.logout(db_session, token)
    return success(message="已退出登录")
