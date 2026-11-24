"""修改本人密码的业务逻辑，负责身份核验和事务保存。"""

from sqlalchemy.orm import Session

from app.core.errors import CurrentPasswordIncorrectError
from app.core.security import decode_access_token, hash_password, verify_password
from app.services.auth import resolve_token_user


# 做什么：验证登录凭证和旧密码，原子更新密码哈希和令牌版本
# 入参：尚未开启事务的会话、登录凭证、旧密码、新密码
# 返回：成功无返回值；凭证无效、账号停用或旧密码错误时抛业务异常
def change_password(
    db_session: Session, token: str, current_password: str, new_password: str
) -> None:
    claims = decode_access_token(token)
    # 新手注意：事务像一次完整交接，成功才落库，途中出错就全部撤回。
    with db_session.begin():
        # 锁住这一行，避免两个改密请求同时用旧密码通过验证后互相覆盖。
        current_user = resolve_token_user(db_session, claims, lock_for_update=True)
        if not verify_password(current_password, current_user.password_hash):
            raise CurrentPasswordIncorrectError()
        current_user.password_hash = hash_password(new_password)
        current_user.token_version += 1
