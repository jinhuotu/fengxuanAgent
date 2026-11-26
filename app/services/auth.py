"""登录业务逻辑。

这里是唯一判断"这个账号能不能登录"的地方：查人、验密、看状态都在这里做完。
api 层只收参数和包响应，不再重复判断，规则只有这一份。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.core.config import get_settings
from app.core.errors import (
    AccountDisabledError,
    InvalidCredentialsError,
    TokenInvalidError,
)
from app.core.security import AccessTokenClaims, create_access_token, decode_access_token, verify_password
from app.models.user import IS_DELETED, USER_STATUS_ACTIVE, User
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
    # 密码哈希和签发版本必须来自同一条查询，防止并发改密时交叉读到新旧值。
    user_query = select(User).options(undefer(User.token_version)).where(User.username == username)
    user = db_session.scalars(user_query).one_or_none()

    # 账号不存在和密码不对共用同一句提示，防止有人拿错误信息反推哪些账号存在
    if user is None:
        raise InvalidCredentialsError()

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()

    # 密码对了但账号已被逻辑删除，和停用同样处理：身份已知、无权进入，给 403
    if user.is_deleted == IS_DELETED:
        raise AccountDisabledError()

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
    return issue_login_response(user)


# 做什么：给账号签发一张登录凭证并换算出秒数有效期
# 入参：已确认可以登录的用户，版本号取它当前的数据
# 返回：LoginResponse，含 access_token、token_type、expires_in（单位秒）
def issue_login_response(user: User) -> LoginResponse:
    access_token = create_access_token(user.id, token_version=user.token_version)
    expires_in = settings.jwt_expire_minutes * SECONDS_PER_MINUTE
    return LoginResponse(access_token=access_token, expires_in=expires_in)


# 做什么：给仍然有效的登录凭证换发一张新的，有效期从现在重新计算
# 入参：db_session 请求级会话；token 客户端带来的 Bearer 凭证
# 返回：LoginResponse，令牌版本保持不变
# 失败：凭证过期或伪造抛 TokenInvalidError；账号停用或已删除抛 AccountDisabledError
# 说明：旧凭证在自身到期前仍然可用。要让某个账号的凭证立刻全部作废，走退出、改密、停用或删除，它们会递增版本
def refresh(db_session: Session, token: str) -> LoginResponse:
    claims = decode_access_token(token)
    user = resolve_token_user(db_session, claims)
    return issue_login_response(user)


# 做什么：按签发版本校验用户，修改凭证的请求可以锁住该用户行
# 入参：数据库会话、验签结果和是否需要行锁
# 返回：仍有效的 User；用户不存在或版本不同抛 TokenInvalidError，停用抛 AccountDisabledError
def resolve_token_user(
    db_session: Session, claims: AccessTokenClaims, *, lock_for_update: bool = False
) -> User:
    user_query = select(User).options(undefer(User.token_version)).where(User.id == claims.user_id)
    if lock_for_update:
        user_query = user_query.with_for_update()
    user = db_session.scalar(user_query.execution_options(populate_existing=True))
    if user is None:
        raise TokenInvalidError()
    if user.token_version != claims.token_version:
        raise TokenInvalidError()
    # 已逻辑删除的账号即使拿着版本正确的凭证也不能继续用
    if user.is_deleted == IS_DELETED or user.status != USER_STATUS_ACTIVE:
        raise AccountDisabledError()
    return user


# 做什么：把请求头里的 token 换成当前用户
# 入参：db_session 请求级会话；token 客户端带来的 JWT 字符串
# 返回：UserInfo
# 失败：验签或过期 -> TokenInvalidError；查不到人或已停用 -> 对应异常
# 为什么单独一层：api 层只管"从请求头取字符串"，验签和查库属于业务，留在这里
def resolve_current_user(db_session: Session, token: str) -> UserInfo:
    claims = decode_access_token(token)
    current_user = resolve_token_user(db_session, claims)
    return UserInfo.model_validate(current_user)


# 做什么：递增本人令牌版本，结束该账号当前所有已签发的登录凭证
# 入参：未开启事务的会话和本人 bearer token
# 返回：无；凭证失效或账号停用时抛对应业务异常
def logout(db_session: Session, token: str) -> None:
    claims = decode_access_token(token)
    with db_session.begin():
        current_user = resolve_token_user(db_session, claims, lock_for_update=True)
        current_user.token_version += 1
