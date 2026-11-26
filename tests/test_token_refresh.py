"""隔离验证登录凭证续期：换发新凭证、旧凭证到期前仍可用、失效账号被拒绝。"""
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DEFAULT_JWT_EXPIRE_MINUTES, get_settings
from app.core.errors import TokenInvalidError
from app.core.security import JWT_ALGORITHM, TOKEN_VERSION_CLAIM, decode_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.user import IS_DELETED, USER_STATUS_DISABLED, User
from app.services import auth as auth_service

TEST_PASSWORD = "isolated-test-password"
SECONDS_PER_MINUTE = 60


# 做什么：创建每个用例独享的 SQLite 文件库，准备两个可以登录的账号
# 入参：pytest 提供的用例临时目录
# 返回：未开启事务的测试会话；结束或异常时关闭会话和连接
@pytest.fixture
def refresh_session(tmp_path: Path) -> Iterator[Session]:
    # 新手注意：数据库文件像一本共享账本，各连接独立记账，提交后的记录都能查到。
    database_url = URL.create("sqlite", database=str(tmp_path / "token_refresh.sqlite3"))
    test_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        with test_sessions.begin() as seed_session:
            password_hash = hash_password(TEST_PASSWORD)
            seed_session.add(User(id=1, username="refresh_owner", password_hash=password_hash))
            seed_session.add(User(id=2, username="second_device", password_hash=password_hash))
        with test_sessions() as test_session:
            yield test_session
    finally:
        test_engine.dispose()


# 做什么：让 HTTP 接口从同一个 SQLite 文件库取得独立会话
# 入参：隔离会话和替换会话工厂的 monkeypatch
# 返回：可调用真实接口的测试客户端，请求结束后关闭
@pytest.fixture
def refresh_client(refresh_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(database_session, "SessionLocal", sessionmaker(bind=refresh_session.bind))
    with TestClient(app) as client:
        yield client


# 做什么：用测试账号登录并取出可以直接放进请求头的凭证
# 入参：测试客户端和账号名，默认是用户 1
# 返回：带 Bearer 凭证的请求头
def login_headers(refresh_client: TestClient, username: str = "refresh_owner") -> dict[str, str]:
    login_body = {"username": username, "password": TEST_PASSWORD}
    login_response = refresh_client.post("/auth/login", json=login_body)
    assert login_response.status_code == 200
    return {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}


# 做什么：读取指定账号当前的令牌版本
# 入参：隔离会话和用户编号
# 返回：数据库里的令牌版本
def read_token_version(refresh_session: Session, user_id: int) -> int:
    version_query = select(User.token_version).where(User.id == user_id)
    return refresh_session.scalar(version_query)


# 做什么：验证续期换发同账号新凭证，旧凭证与另一台设备的凭证都不受影响
# 入参：隔离会话和测试客户端
# 返回：新凭证有效期、账号标识或旧凭证状态不符时断言失败
def test_refresh_issues_new_token_for_same_account(
    refresh_session: Session, refresh_client: TestClient,
) -> None:
    original_headers = login_headers(refresh_client)
    other_device_headers = login_headers(refresh_client)
    original_token = original_headers["Authorization"].removeprefix("Bearer ")

    refreshed = refresh_client.post("/auth/refresh", headers=original_headers)

    assert refreshed.status_code == 200
    refreshed_data = refreshed.json()["data"]
    assert refreshed_data["expires_in"] == DEFAULT_JWT_EXPIRE_MINUTES * SECONDS_PER_MINUTE
    # 签发时间精确到秒，登录和续期落在同一秒时两张凭证逐字相同，所以不比较字符串，
    # 只确认新凭证属于同一账号、可以用，旧凭证也不受影响
    assert "access_token" in refreshed_data
    original_claims = decode_access_token(original_token)
    new_claims = decode_access_token(refreshed_data["access_token"])
    assert (new_claims.user_id, new_claims.token_version) == (1, original_claims.token_version)
    new_headers = {"Authorization": f"Bearer {refreshed_data['access_token']}"}
    assert refresh_client.get("/auth/me", headers=new_headers).status_code == 200
    # 续期不作废旧凭证：它在自己到期之前仍然有效
    assert refresh_client.get("/auth/me", headers=original_headers).status_code == 200
    assert refresh_client.get("/auth/me", headers=other_device_headers).status_code == 200
    assert read_token_version(refresh_session, 1) == original_claims.token_version


# 做什么：伪造一枚已经过期的凭证，用来确认续期接口不放过过期凭证
# 入参：无，使用测试配置里的签名密钥
# 返回：过期时间停在 1970 年的凭证字符串
def expired_token() -> str:
    payload = {"sub": "1", TOKEN_VERSION_CLAIM: 0, "exp": 1}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


# 做什么：验证凭证缺失、伪造或过期时不能续期
# 入参：隔离会话、测试客户端和凭证问题类型
# 返回：未返回 401/1002 时断言失败
@pytest.mark.parametrize("case", ["missing", "forged", "expired"])
def test_refresh_rejects_invalid_token(
    refresh_session: Session, refresh_client: TestClient, case: str,
) -> None:
    headers: dict[str, str] = {}
    if case == "forged":
        headers = {"Authorization": "Bearer not-a-real-token"}
    if case == "expired":
        headers = {"Authorization": f"Bearer {expired_token()}"}

    response = refresh_client.post("/auth/refresh", headers=headers)

    assert (response.status_code, response.json()["code"]) == (401, 1002)


# 做什么：验证退出登录后旧凭证不能用来续期
# 入参：隔离会话和测试客户端
# 返回：已退出的凭证仍能续期时断言失败
def test_refresh_rejects_token_after_logout(refresh_session: Session, refresh_client: TestClient) -> None:
    headers = login_headers(refresh_client)
    assert refresh_client.post("/auth/logout", headers=headers).status_code == 200

    response = refresh_client.post("/auth/refresh", headers=headers)

    assert (response.status_code, response.json()["code"]) == (401, 1002)
    assert read_token_version(refresh_session, 1) == 1


# 做什么：验证账号被停用或逻辑删除后续期被拒绝
# 入参：隔离会话、测试客户端和停用方式
# 返回：未返回 403/1003 时断言失败
@pytest.mark.parametrize("removal", ["disabled", "deleted"])
def test_refresh_rejects_removed_account(
    refresh_session: Session, refresh_client: TestClient, removal: str,
) -> None:
    headers = login_headers(refresh_client)
    with refresh_session.begin():
        target_user = refresh_session.get(User, 1)
        assert target_user is not None
        if removal == "disabled":
            target_user.status = USER_STATUS_DISABLED
        else:
            target_user.is_deleted = IS_DELETED

    response = refresh_client.post("/auth/refresh", headers=headers)

    assert (response.status_code, response.json()["code"]) == (403, 1003)


# 做什么：验证服务层直接调用时同样拒绝无效凭证，且没有留下未结束的事务
# 入参：隔离会话
# 返回：未抛业务异常或留下未结束的事务时断言失败
def test_refresh_service_rejects_invalid_token(refresh_session: Session) -> None:
    with pytest.raises(TokenInvalidError):
        auth_service.refresh(refresh_session, "not-a-real-token")
    assert not refresh_session.in_transaction()
