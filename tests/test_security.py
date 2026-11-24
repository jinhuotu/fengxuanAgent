"""密码哈希与 token 签发层的测试。

测的不是"函数能跑"，而是"行为不许变"：同一个密码两次哈希结果不同、
错误密码验不过、改一个字符的 token 验不过。这些一旦被改坏，登录就有安全问题。
"""

import bcrypt
import jwt
import pytest
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.errors import PasswordHashUnavailableError, TokenInvalidError
from app.core.security import (
    JWT_ALGORITHM,
    PASSWORD_HASH_PREFIX,
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
    assert verify_password("anything", PASSWORD_HASH_PREFIX + "not-a-bcrypt-hash") is False


# 做什么：确认已经退役的无版本 bcrypt 哈希不再参与认证
# 入参：无，使用测试专用密码构造一条无版本哈希
# 返回：无；还接受无版本哈希时断言失败
def test_unversioned_password_hash_is_rejected() -> None:
    password = "legacy-test-password"
    unversioned_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    assert not verify_password(password, unversioned_hash)


# 做什么：拒绝字符数合规但 UTF-8 字节超限的待存密码
# 入参：无，使用三个字节的汉字构造超限输入
# 返回：无；哈希入口未拒绝超限输入时断言失败
def test_hash_rejects_password_over_utf8_byte_limit() -> None:
    with pytest.raises(ValueError, match="128"):
        hash_password("密" * 43)


# 做什么：确认新版哈希覆盖完整密码，且能放进现有列
# 入参：无，使用恰好 128 字节的测试密码
# 返回：无；长度边界或校验结果不符时断言失败
def test_versioned_hash_uses_the_complete_password() -> None:
    long_password = "密" * 42 + "ab"
    password_hash = hash_password(long_password)
    assert password_hash.startswith(PASSWORD_HASH_PREFIX)
    assert len(password_hash) <= 100
    assert verify_password(long_password, password_hash)
    assert not verify_password(long_password + "c", password_hash)
    assert not verify_password("密" * 42 + "ac", password_hash)


# 做什么：确认缺少密钥时新版哈希无法读写
# 入参：monkeypatch 临时清空隔离配置里的密钥
# 返回：无；未报配置问题时断言失败
def test_versioned_hash_requires_a_pepper(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "configured-test-password"
    password_hash = hash_password(password)
    monkeypatch.setattr(settings, "password_pepper", SecretStr(""))
    with pytest.raises(PasswordHashUnavailableError):
        hash_password(password)
    with pytest.raises(PasswordHashUnavailableError):
        verify_password(password, password_hash)


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
