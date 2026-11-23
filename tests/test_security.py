"""密码哈希与 token 签发层的测试。

测的不是"函数能跑"，而是"行为不许变"：同一个密码两次哈希结果不同、
错误密码验不过、改一个字符的 token 验不过。这些一旦被改坏，登录就有安全问题。
"""

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import TokenInvalidError
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

settings = get_settings()


# 做什么：确认同一密码两次哈希结果不同（说明盐每次都是新的）
# 入参：无
# 返回：无；断言失败则测试红
def test_same_password_hashes_differently() -> None:
    first_hash = hash_password("P@ssw0rd")
    second_hash = hash_password("P@ssw0rd")
    assert first_hash != second_hash
    assert verify_password("P@ssw0rd", first_hash)
    assert verify_password("P@ssw0rd", second_hash)


# 做什么：确认错误密码验不过，且不抛异常
# 入参：无
# 返回：无
def test_wrong_password_fails_without_raising() -> None:
    correct_hash = hash_password("right-password")
    assert verify_password("wrong-password", correct_hash) is False


# 做什么：确认哈希串被改坏时按"验证失败"处理，而不是抛 ValueError
# 入参：无
# 返回：无
def test_broken_hash_returns_false() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False


# 做什么：确认 token 签出去能原样解回来
# 入参：无
# 返回：无
def test_token_round_trip() -> None:
    token = create_access_token(user_id=42)
    assert decode_access_token(token) == 42


# 做什么：确认被篡改的 token 验不过
# 入参：无
# 返回：无；应抛 TokenInvalidError（对外是 401 + code 1002）
def test_tampered_token_is_rejected() -> None:
    token = create_access_token(user_id=7)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(TokenInvalidError):
        decode_access_token(tampered)


# 做什么：确认过期 token 验不过
# 入参：无
# 返回：无
def test_expired_token_is_rejected() -> None:
    expired = jwt.encode(
        {"sub": "1", "exp": 1},
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(TokenInvalidError):
        decode_access_token(expired)
