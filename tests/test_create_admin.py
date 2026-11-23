"""验证初始化脚本创建的账号可以使用管理员接口。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import session as database_session
from app.main import app
from app.models.user import User
from scripts import create_admin as admin_script


# 做什么：让初始化脚本和接口共用一个隔离的内存用户表。
# 入参：monkeypatch 用来临时替换两处会话工厂。
# 返回：测试期间提供会话，结束或出错时释放连接。
@pytest.fixture
def isolated_user_sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # 新手注意：内存库像一块白板，脚本和请求要用同一块才能看到同一行。
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        User.__table__.create(test_engine)
        test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
        monkeypatch.setattr(admin_script, "SessionLocal", test_sessions)
        monkeypatch.setattr(database_session, "SessionLocal", test_sessions)
        yield
    finally:
        test_engine.dispose()


# 做什么：确认首个账号可登录、建用户，并被识别为管理员。
# 入参：isolated_user_sessions 提供空的用户表。
# 返回：无，初始化或鉴权行为不符时测试失败。
def test_first_admin_can_create_users(isolated_user_sessions: None) -> None:
    username = "first_admin"
    password = "bootstrap-test-password"
    admin_script.create_admin(username, password, display_name=None)
    admin_script.create_admin(username, "replacement-test-password", display_name=None)

    with TestClient(app) as client:
        login_response = client.post("/auth/login", json={"username": username, "password": password})
        assert login_response.status_code == 200
        access_token = login_response.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}
        create_response = client.post(
            "/users", headers=headers, json={"username": "normal_user", "password": password}
        )
        assert create_response.status_code == 200
        assert create_response.json()["data"]["is_admin"] is False

        current_user_response = client.get("/auth/me", headers=headers)
        assert current_user_response.status_code == 200
        assert current_user_response.json()["data"]["is_admin"] is True
