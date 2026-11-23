"""登录业务逻辑。

这里是唯一判断"这个账号能不能登录"的地方：查人、验密、看状态都在这里做完。
api 层只收参数和包响应，不再重复判断，规则只有这一份。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    AccountDisabledError,
    InvalidCredentialsError,
    TokenInvalidError,
)
from app.core.security import create_access_token, decode_access_token, verify_password
from app.models.user import USER_STATUS_ACTIVE, User
from app.schemas.auth import LoginResponse, UserInfo
from app.services import login_guard

settings = get_settings()

# 一分钟多少秒：把配置里的"分钟"换成前端要的"秒"，换算只在这一处做
SECONDS_PER_MINUTE = 60


# 做什么：按用户名查人，校验密码和账号状态
# 入参：db_session 请求级会话；username 登录名；password 本次输入的明文密码
# 返回：校验通过的 User 对象
# 失败：账号不存在或密码不对抛 InvalidCredentialsError；账号停用抛 AccountDisabledError
def authenticate_user(db_session: Session, username: str, password: str) -> User:
    # 新手注意：scalars 把结果行拆成单个对象，one_or_none 是"零行给 None，多行直接报错"
    # 注意 Session 本身没有 one_or_none，这个方法在 scalars 返回的结果对象上，顺序不能反
    user = db_session.scalars(select(User).where(User.username == username)).one_or_none()

    # 账号不存在和密码不对共用同一句提示，防止有人拿错误信息反推哪些账号存在
    if user is None:
        raise InvalidCredentialsError()

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()

    # 密码对了但账号被管理员停用，属于"身份已知、无权进入"，单独给 403
    if user.status != USER_STATUS_ACTIVE:
        raise AccountDisabledError()

    return user


# 做什么：登录入口，先查锁，再校验身份，最后签发 token
# 入参：db_session 请求级会话；username 登录名；password 明文密码
# 返回：LoginResponse，含 access_token、token_type、expires_in（单位秒）
# 失败：锁定中 429；账号不存在、密码不对、账号停用，异常由全局处理器转成 401 / 403
def login(db_session: Session, username: str, password: str) -> LoginResponse:
    # 先查锁：被锁的账号连密码都不比，否则锁定期间还能继续试探
    login_guard.ensure_not_locked(username)

    try:
        user = authenticate_user(db_session, username=username, password=password)
    except InvalidCredentialsError:
        # 捕获后原样重抛，不是吞异常：只是顺手记一次失败，再交给全局处理器转 401
        login_guard.record_failure(username)
        raise

    login_guard.clear_failures(username)
    access_token = create_access_token(user.id)
    expires_in = settings.jwt_expire_minutes * SECONDS_PER_MINUTE
    return LoginResponse(access_token=access_token, expires_in=expires_in)


# 做什么：按主键取用户，并确认账号现在仍然可用
# 入参：db_session 请求级会话；user_id 从 token 里解出来的用户 id
# 返回：UserInfo，只含前端需要的列
# 失败：账号已被删 -> TokenInvalidError；账号被停用 -> AccountDisabledError
# 为什么每次请求都查一次库：token 有效期内账号可能被停用，不能只信纸面
def get_active_user(db_session: Session, user_id: int) -> UserInfo:
    # 新手注意：get 是"按主键直接翻到那一页"，比 select + where 少一层，主键查询用它最省事
    user = db_session.get(User, user_id)
    if user is None:
        raise TokenInvalidError()

    if user.status != USER_STATUS_ACTIVE:
        raise AccountDisabledError()

    return UserInfo.model_validate(user)


# 做什么：把请求头里的 token 换成当前用户
# 入参：db_session 请求级会话；token 客户端带来的 JWT 字符串
# 返回：UserInfo
# 失败：验签或过期 -> TokenInvalidError；查不到人或已停用 -> 对应异常
# 为什么单独一层：api 层只管"从请求头取字符串"，验签和查库属于业务，留在这里
def resolve_current_user(db_session: Session, token: str) -> UserInfo:
    user_id = decode_access_token(token)
    return get_active_user(db_session, user_id)
