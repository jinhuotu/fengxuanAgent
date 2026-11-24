"""密码哈希与 token 签发。

这两件事都放在 core：它们与业务无关，任何模块需要"加密密码"或"确认身份"都从这里取。
换哈希算法、换 token 算法，只改这一个文件，不用满项目搜。

两个概念先记住：
bcrypt：哈希自带随机盐，所以同一个密码每次结果都不同，只能比对不能还原。
JWT：字典 → 三段点分字符串（头.体.签名），服务端用密钥验签名，不用存库也能确认"这封介绍信没被改过"。
"""

import hmac
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import PasswordHashUnavailableError, TokenInvalidError
from app.core.password_policy import MAX_PASSWORD_BYTES, validate_password_byte_limit

settings = get_settings()

# 签名算法。HS256 是"同一个密钥既签又验"的对称算法，单服务够用；
# 换成非对称算法（RS256）要额外管公私钥，阶段一没必要
JWT_ALGORITHM = "HS256"

PASSWORD_HASH_PREFIX = "bcrypt-hmac-sha384-v1:"
PASSWORD_PREHASH_ALGORITHM = "sha384"
MIN_PASSWORD_PEPPER_BYTES = 32


# 做什么：对完整密码做带密钥的预哈希
# 入参：密码的完整 UTF-8 字节
# 返回：64 个 Base64 字节；密钥不合格时抛出 PasswordHashUnavailableError
# 新手注意：HMAC 像用服务器独有的印章给整封信盖章，信的末尾变化也会改变结果。
def _prehash_password(password_bytes: bytes) -> bytes:
    pepper_bytes = settings.password_pepper.get_secret_value().encode("utf-8")
    if len(pepper_bytes) < MIN_PASSWORD_PEPPER_BYTES:
        raise PasswordHashUnavailableError()
    return b64encode(hmac.digest(pepper_bytes, password_bytes, PASSWORD_PREHASH_ALGORITHM))


# 做什么：把明文密码变成哈希串，准备存进 users.password_hash
# 入参：用户输入的明文密码
# 返回：带算法版本和随机盐的哈希；超限或缺少密钥时抛出异常
def hash_password(plain_password: str) -> str:
    validate_password_byte_limit(plain_password)
    bcrypt_input = _prehash_password(plain_password.encode("utf-8"))
    password_hash = bcrypt.hashpw(bcrypt_input, bcrypt.gensalt()).decode("ascii")
    return PASSWORD_HASH_PREFIX + password_hash


# 做什么：用完整密码校验带版本标记的哈希
# 入参：用户输入的明文和数据库中的哈希
# 返回：匹配时 True；密码错误、超限或格式不支持时 False；密钥不可用时抛出异常
def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash.startswith(PASSWORD_HASH_PREFIX):
        return False
    try:
        password_bytes = plain_password.encode("utf-8")
        if len(password_bytes) > MAX_PASSWORD_BYTES:
            return False
        bcrypt_hash = password_hash.removeprefix(PASSWORD_HASH_PREFIX)
        return bcrypt.checkpw(_prehash_password(password_bytes), bcrypt_hash.encode("ascii"))
    except ValueError:
        # bcrypt 无法识别的哈希或无法编码的输入都按校验失败处理。
        return False


# 做什么：把用户 id 签成 token，交给客户端带着
# 入参：user_id，数据库里的用户主键
# 返回：JWT 字符串，三段点分
# 为什么 sub 存字符串：JWT 规范要求 sub 是字符串，存 int 有些库校验会报错
# 为什么 payload 只有三项：只放"是谁、何时签发、何时失效"，
# 不放密码、手机号、角色名——JWT 只做了签名没有加密，任何人都能 base64 解出内容
def create_access_token(user_id: int) -> str:
    issued_at = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


# 做什么：验证 token 并取出用户 id
# 入参：客户端带来的 token 字符串
# 返回：用户 id（int）
# 失败：签名不对、过期、结构不对，一律抛 TokenInvalidError（转 401，code 1002）
# 为什么统一抛同一个错：前端只需要知道"重新登录"，不该区分是过期还是伪造
def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError() from exc

    user_id: Any = payload.get("sub")
    if user_id is None:
        raise TokenInvalidError()
    try:
        return int(user_id)
    except (TypeError, ValueError) as exc:
        raise TokenInvalidError() from exc
