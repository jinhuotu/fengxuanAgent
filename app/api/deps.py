"""路由共用的依赖。

这里只做 HTTP 关心的事：从请求头把 token 取出来。
验签、查库、判断账号状态，全部转交给 services，api 层不碰这些。
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import PermissionDeniedError, TokenInvalidError
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.services import auth as auth_service

# auto_error=False：没带 token 时不让框架直接丢一个 403，
# 改成我们自己抛 TokenInvalidError，保证"没带 / 格式错 / 过期"都是同一个 code 1002
bearer_scheme = HTTPBearer(auto_error=False)


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


# 做什么：在"已登录"的基础上再要求这个人是管理员
# 入参：current_user 由 get_current_user 提供（已验过 token、已查过库、状态已确认 active）
# 返回：UserInfo，管理员本人
# 失败：不是管理员 -> PermissionDeniedError（403 / 2001）
# 说明：阶段一没有角色表，直接看 users.is_admin；阶段二把它换成"查角色"就行，
# 调用方（具体接口）不用改，因为它们只认这个依赖名
def get_current_admin(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if not current_user.is_admin:
        raise PermissionDeniedError()
    return current_user
