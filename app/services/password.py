"""修改本人密码的业务逻辑，负责身份核验和事务保存。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    AccountDisabledError,
    CurrentPasswordIncorrectError,
    TokenInvalidError,
)
from app.core.security import decode_access_token, hash_password, verify_password
from app.models.user import USER_STATUS_ACTIVE, User


# 做什么：验证登录凭证和旧密码，保存本人新密码的哈希
# 入参：尚未开启事务的会话、登录凭证、旧密码、新密码
# 返回：成功无返回值；凭证无效、账号停用或旧密码错误时抛业务异常
def change_password(
    db_session: Session, token: str, current_password: str, new_password: str
) -> None:
    user_id = decode_access_token(token)
    # 新手注意：事务像一次完整交接，成功才落库，途中出错就全部撤回。
    with db_session.begin():
        # 锁住这一行，避免两个改密请求同时用旧密码通过验证后互相覆盖。
        user_query = select(User).where(User.id == user_id).with_for_update()
        current_user = db_session.scalar(user_query)
        if current_user is None:
            raise TokenInvalidError()
        if current_user.status != USER_STATUS_ACTIVE:
            raise AccountDisabledError()
        if not verify_password(current_password, current_user.password_hash):
            raise CurrentPasswordIncorrectError()
        current_user.password_hash = hash_password(new_password)
