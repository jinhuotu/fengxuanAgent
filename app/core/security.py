"""密码哈希与 token 签发。

这两件事都放在 core：它们与业务无关，任何模块需要"加密密码"或"确认身份"都从这里取。
换哈希算法、换 token 算法，只改这一个文件，不用满项目搜。

两个概念先记住：
bcrypt：密码 → 60 字符哈希串，里面自带随机盐，所以同一个密码每次结果都不同，只能比对不能还原。
JWT：字典 → 三段点分字符串（头.体.签名），服务端用密钥验签名，不用存库也能确认"这封介绍信没被改过"。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import TokenInvalidError

settings = get_settings()

# 签名算法。HS256 是"同一个密钥既签又验"的对称算法，单服务够用；
# 换成非对称算法（RS256）要额外管公私钥，阶段一没必要
JWT_ALGORITHM = "HS256"

# bcrypt 只处理密码的前 72 字节，第 73 个字节起直接丢弃；
# bcrypt 5.x 遇到超长密码会直接报错，所以这里显式截断，保证哈希和校验口径一致
BCRYPT_MAX_BYTES = 72


# 做什么：把明文密码转成 bcrypt 需要的字节，并截断到 72 字节以内
# 入参：任何字符串密码（中文按 utf-8 编码后可能比字符数长）
# 返回：不超过 72 字节的 bytes
def _to_bcrypt_bytes(plain_password: str) -> bytes:
    return plain_password.encode("utf-8")[:BCRYPT_MAX_BYTES]


# 做什么：把明文密码变成哈希串，准备存进 users.password_hash
# 入参：用户输入的明文密码
# 返回：60 字符左右的哈希串，自带盐；同样的密码每次调用结果都不同
# 新手注意：就像把纸塞进碎纸机，出来的碎片拼不回原文；加盐等于每次碎之前先撒一把不同的沙子
def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(plain_password), bcrypt.gensalt()).decode("utf-8")


# 做什么：校验明文密码与数据库里的哈希是否匹配
# 入参：用户这次输入的明文、库里存的哈希串
# 返回：True 匹配；False 不匹配（包括哈希串被手改坏的情况）
# 为什么返回布尔而不是抛异常：密码对不对是登录流程里的一个判断，由 service 决定怎么提示
def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain_password), password_hash.encode("utf-8"))
    except ValueError:
        # 哈希串格式不对（长度不够、不是 bcrypt 格式）时 checkpw 会抛 ValueError，
        # 这种情况等于"验证不通过"，不该让整个请求 500
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
