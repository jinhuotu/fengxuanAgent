"""路由共用的依赖。

这里只做 HTTP 关心的事：从请求头把 token 取出来。
验签、查库、判断账号状态，全部转交给 services，api 层不碰这些。
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import TokenInvalidError
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.services import auth as auth_service
from app.services import rbac as rbac_service

# auto_error=False：没带 token 时不让框架直接丢一个 403，
# 改成我们自己抛 TokenInvalidError，保证"没带 / 格式错 / 过期"都是同一个 code 1002
bearer_scheme = HTTPBearer(auto_error=False)


# 做什么：只提取登录凭证，不查库、不启动事务
# 入参：请求头中的 Bearer 凭证，未提供时为 None
# 返回：token 字符串；凭证缺失或格式不符时抛 TokenInvalidError
def get_access_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None:
        raise TokenInvalidError()
    return credentials.credentials


# 做什么：从 Authorization 头取出 token，换成当前登录用户
# 入参：credentials 已解析的请求头（可能为 None）；db_session 请求级会话
# 返回：UserInfo，路由函数直接拿去用
# 失败：没有 Authorization 头或不是 Bearer 格式 -> TokenInvalidError（401 / 1002）
# 说明：验签与查库在 service 里，这里只判断"有没有带来凭证"
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db_session: Session = Depends(get_db),
) -> UserInfo:
    if credentials is None:
        raise TokenInvalidError()

    return auth_service.resolve_current_user(db_session, credentials.credentials)


# 做什么：将当前用户交给服务检查是否拥有创建用户权限
# 入参：已确认身份的用户和请求级数据库会话
# 返回：有权限的用户；未登录、账号停用或无权限时抛对应业务异常
def require_user_creation_permission(
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> UserInfo:
    rbac_service.ensure_permission(db_session, current_user.id, rbac_service.PERMISSION_USERS_CREATE)
    return current_user


# 做什么：将当前用户交给服务检查是否拥有查看用户列表的权限
# 入参：已确认身份的用户和请求级数据库会话
# 返回：有权限的用户；未登录、停用或无权限时抛对应业务异常
def require_user_list_permission(
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> UserInfo:
    rbac_service.ensure_permission(db_session, current_user.id, rbac_service.PERMISSION_USERS_LIST)
    return current_user


# 做什么：将当前用户交给服务检查是否拥有追加角色的权限
# 入参：已确认身份的用户和请求级数据库会话
# 返回：有权限的用户；未登录、停用或无权限时抛对应业务异常
def require_role_assign_permission(
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> UserInfo:
    rbac_service.ensure_permission(
        db_session, current_user.id, rbac_service.PERMISSION_USERS_ASSIGN_ROLE
    )
    return current_user


# 做什么：将当前用户交给服务检查是否拥有切换用户状态的权限
# 入参：已验证身份的用户和请求级数据库会话
# 返回：有权限的用户；未登录、停用或无权限时抛对应业务异常
def require_user_disable_permission(
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> UserInfo:
    rbac_service.ensure_permission(db_session, current_user.id, rbac_service.PERMISSION_USERS_DISABLE)
    return current_user
