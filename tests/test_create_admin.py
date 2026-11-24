"""验证初始化脚本创建的账号可以使用管理员接口。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import session as database_session
from app.db.base import Base
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserRole
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
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
        with test_sessions.begin() as seed_session:
            seed_session.add(Role(id=1, code="admin", name="管理员"))
            seed_session.add(Role(id=2, code="user", name="普通用户"))
            seed_session.add(Role(id=3, code="reader", name="只读测试角色"))
            seed_session.add(Permission(id=1, code="users:create", name="新建用户"))
            seed_session.add(Permission(id=2, code="users:list", name="查看用户列表"))
            seed_session.add(Permission(id=3, code="users:assign_role", name="授予或撤销用户角色"))
            seed_session.flush()
            seed_session.add(RolePermission(role_id=1, permission_id=1))
            seed_session.add(RolePermission(role_id=1, permission_id=2))
            seed_session.add(RolePermission(role_id=1, permission_id=3))
            seed_session.add(RolePermission(role_id=3, permission_id=2))
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
        assert_initial_role_authorization(client, headers)
        create_response = client.post(
            "/users", headers=headers, json={"username": "normal_user", "password": password}
        )
        assert create_response.status_code == 200
        assert create_response.json()["data"]["is_admin"] is False

        current_user_response = client.get("/auth/me", headers=headers)
        assert current_user_response.status_code == 200
        assert current_user_response.json()["data"]["is_admin"] is True

        new_headers = assert_password_change_flow(client, username, password, headers)
        assert_user_list_flow(client, new_headers, create_response.json()["data"]["id"])
        assert_role_binding_flow(client, new_headers, create_response.json()["data"]["id"])


# 做什么：验证管理员登录、校验当前密码和设置新密码共用字节上限。
# 入参：isolated_user_sessions 提供隔离内存库。
# 返回：无；128 字节无法使用或 129 字节未被拒绝时断言失败。
def test_password_byte_limit_applies_to_login_and_change(isolated_user_sessions: None) -> None:
    username = "boundary_admin"
    password = "旧" * 42 + "ab"
    new_password = "密" * 42 + "ab"
    admin_script.create_admin(username, password, display_name=None)
    with TestClient(app) as client:
        oversized_login = {"username": username, "password": password + "c"}
        assert client.post("/auth/login", json=oversized_login).status_code == 422
        login_response = client.post("/auth/login", json={"username": username, "password": password})
        assert login_response.status_code == 200
        headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}
        oversized_current = {"current_password": password + "c", "new_password": new_password}
        oversized_new = {"current_password": password, "new_password": new_password + "c"}
        for change_body in (oversized_current, oversized_new):
            invalid_response = client.post("/auth/change-password", headers=headers, json=change_body)
            assert invalid_response.status_code == 422
            assert "128" in invalid_response.json()["message"]
        change_body = {"current_password": password, "new_password": new_password}
        change_response = client.post("/auth/change-password", headers=headers, json=change_body)
        assert change_response.status_code == 200
        assert client.post("/auth/login", json={"username": username, "password": password}).status_code == 401
        assert client.post("/auth/login", json={"username": username, "password": new_password}).status_code == 200


# 做什么：验证改密后，改密前签发的 token 立即失效
# 入参：独立内存库会话
# 返回：无；旧 token 未返回 401/1002 或新 token 无法使用时断言失败
def test_password_change_rejects_old_token(isolated_user_sessions: None) -> None:
    username = "password_version_admin"
    password = "old-test-password"
    new_password = "new-test-password"
    admin_script.create_admin(username, password, display_name=None)
    with TestClient(app) as client:
        original_login = client.post("/auth/login", json={"username": username, "password": password})
        assert original_login.status_code == 200
        old_headers = {"Authorization": f"Bearer {original_login.json()['data']['access_token']}"}
        assert client.get("/auth/me", headers=old_headers).status_code == 200
        change_body = {"current_password": password, "new_password": new_password}
        changed = client.post("/auth/change-password", headers=old_headers, json=change_body)
        assert changed.status_code == 200
        expired = client.get("/auth/me", headers=old_headers)
        assert (expired.status_code, expired.json()["code"]) == (401, 1002)
        replayed = client.post("/auth/change-password", headers=old_headers, json=change_body)
        assert (replayed.status_code, replayed.json()["code"]) == (401, 1002)
        new_login = client.post("/auth/login", json={"username": username, "password": new_password})
        assert new_login.status_code == 200
        new_headers = {"Authorization": f"Bearer {new_login.json()['data']['access_token']}"}
        assert client.get("/auth/me", headers=new_headers).status_code == 200


# 做什么：验证服务端退出递增版本，退出前的 token 立即失效
# 入参：独立内存库会话
# 返回：无；退出失败或旧 token 仍能访问时断言失败
def test_logout_rejects_old_token(isolated_user_sessions: None) -> None:
    username = "logout_version_admin"
    password = "logout-test-password"
    admin_script.create_admin(username, password, display_name=None)
    with TestClient(app) as client:
        login_body = {"username": username, "password": password}
        login_response = client.post("/auth/login", json=login_body)
        assert login_response.status_code == 200
        headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}
        assert client.post("/auth/logout", headers=headers).status_code == 200
        expired = client.get("/auth/me", headers=headers)
        assert (expired.status_code, expired.json()["code"]) == (401, 1002)
        assert client.post("/auth/login", json=login_body).status_code == 200


# 做什么：确认内存模式按原规则连续失败 5 次后锁定
# 入参：隔离内存库和用于关闭 Redis 的 monkeypatch
# 返回：无；前 5 次不是 401/1001 或第 6 次不是 429/1004 就断言失败
def test_sixth_login_is_locked_in_memory_mode(
    isolated_user_sessions: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "redis_enabled", False)
    username = "memory_lock_admin"
    password = "valid-memory-test-password"
    admin_script.create_admin(username, password, display_name=None)
    with TestClient(app) as client:
        wrong_login = {"username": username, "password": "incorrect-test-password"}
        for attempt in range(5):
            failed_login = client.post("/auth/login", json=wrong_login)
            assert (failed_login.status_code, failed_login.json()["code"]) == (401, 1001)
        locked_login = client.post("/auth/login", json={"username": username, "password": password})
        assert (locked_login.status_code, locked_login.json()["code"]) == (429, 1004)


# 做什么：检查改密接口的拒绝响应和成功后的登录行为
# 入参：隔离客户端、登录名、旧密码和登录请求头
# 返回：改密后重新登录的认证头；错误码或登录行为不符时断言失败
def assert_password_change_flow(
    client: TestClient, username: str, password: str, headers: dict[str, str]
) -> dict[str, str]:
    replacement_password = "replacement-test-password"
    change_body = {"current_password": password, "new_password": replacement_password}
    missing_token_response = client.post("/auth/change-password", json=change_body)
    assert missing_token_response.status_code == 401
    assert missing_token_response.json()["code"] == 1002
    wrong_body = {"current_password": "wrong-test-password", "new_password": replacement_password}
    wrong_password_response = client.post("/auth/change-password", headers=headers, json=wrong_body)
    assert wrong_password_response.status_code == 400
    assert wrong_password_response.json()["code"] == 1006
    change_response = client.post("/auth/change-password", headers=headers, json=change_body)
    assert change_response.status_code == 200
    assert change_response.json() == {"code": 0, "message": "密码修改成功，请重新登录", "data": None}
    # 重新登录核验落库结果，避免接口只返回成功却未实际更新。
    old_login_response = client.post("/auth/login", json={"username": username, "password": password})
    assert old_login_response.status_code == 401
    new_login_response = client.post("/auth/login", json={"username": username, "password": replacement_password})
    assert new_login_response.status_code == 200
    return {"Authorization": f"Bearer {new_login_response.json()['data']['access_token']}"}


# 做什么：验证角色绑定接口的管理员门禁与重复绑定响应
# 入参：隔离客户端、管理员请求头和普通用户编号
# 返回：无；越权放行、响应或幂等行为不符时断言失败
def assert_role_binding_flow(client: TestClient, headers: dict[str, str], user_id: int) -> None:
    binding_path = f"/users/{user_id}/roles"
    role_body = {"role_code": "admin"}
    assert client.post(binding_path, json=role_body).status_code == 401
    normal_headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    forbidden_response = client.post(binding_path, headers=normal_headers, json=role_body)
    assert forbidden_response.status_code == 403
    assert forbidden_response.json()["code"] == 2001
    create_body = {"username": "permission_created_user", "password": "permission-test-password"}
    assert client.post("/users", headers=normal_headers, json=create_body).status_code == 403
    first_response = client.post(binding_path, headers=headers, json=role_body)
    assert first_response.status_code == 200
    assert first_response.json()["data"] == {"created": True}
    assert client.post("/users", headers=normal_headers, json=create_body).status_code == 200
    repeated_response = client.post(binding_path, headers=headers, json=role_body)
    assert repeated_response.status_code == 200
    assert repeated_response.json()["data"] == {"created": False}
    missing_role_response = client.post(binding_path, headers=headers, json={"role_code": "missing-role"})
    assert missing_role_response.status_code == 404
    assert missing_role_response.json()["code"] == 2002


# 做什么：验证只有管理员标记时没有权限，隔离库预置 admin 角色后才有权限
# 入参：隔离客户端及管理员登录请求头
# 返回：无；未授权放行或角色绑定失败时断言失败
def assert_initial_role_authorization(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/auth/me/permissions").status_code == 401
    assert client.get("/auth/me/permissions", headers=headers).json()["data"] == {"permissions": []}
    create_body = {"username": "normal_user", "password": "bootstrap-test-password"}
    denied_response = client.post("/users", headers=headers, json=create_body)
    assert denied_response.status_code == 403
    assert denied_response.json()["code"] == 2001
    current_user_response = client.get("/auth/me", headers=headers)
    assert current_user_response.status_code == 200
    user_id = current_user_response.json()["data"]["id"]
    denied_binding = client.post(f"/users/{user_id}/roles", headers=headers, json={"role_code": "admin"})
    assert (denied_binding.status_code, denied_binding.json()["code"]) == (403, 2001)
    with database_session.SessionLocal.begin() as seed_session:
        seed_session.add(UserRole(user_id=user_id, role_id=1))


# 做什么：验证用户列表的鉴权、分页、公开字段和参数校验
# 入参：隔离客户端、管理员请求头和无角色的普通用户编号
# 返回：无；状态码、分页结果或公开字段不符时断言失败
def assert_user_list_flow(client: TestClient, headers: dict[str, str], user_id: int) -> None:
    permission_response = client.get("/auth/me/permissions", headers=headers)
    assert permission_response.status_code == 200
    assert permission_response.json() == {
        "code": 0, "message": "成功",
        "data": {"permissions": ["users:assign_role", "users:create", "users:list"]},
    }
    assert client.get("/users").status_code == 401
    normal_headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    assert client.get("/auth/me/permissions", headers=normal_headers).json()["data"] == {"permissions": []}
    forbidden_response = client.get("/users", headers=normal_headers)
    assert forbidden_response.status_code == 403
    assert forbidden_response.json()["code"] == 2001
    first_response = client.get("/users", headers=headers, params={"page": 1, "page_size": 1})
    assert first_response.status_code == 200
    first_page = first_response.json()["data"]
    assert set(first_page) == {"items", "total", "page", "page_size"}
    assert (first_page["total"], first_page["page"], first_page["page_size"]) == (2, 1, 1)
    assert len(first_page["items"]) == 1
    assert first_page["items"][0]["id"] == 1
    assert set(first_page["items"][0]) == {"id", "username", "display_name", "status", "roles"}
    assert first_page["items"][0]["roles"] == [{"code": "admin", "name": "管理员"}]
    reader_binding = client.post(f"/users/{user_id}/roles", headers=headers, json={"role_code": "reader"})
    assert reader_binding.status_code == 200
    assert client.get("/users", headers=normal_headers).status_code == 200
    second_response = client.get("/users", headers=headers, params={"page": 2, "page_size": 1})
    assert second_response.status_code == 200
    assert second_response.json()["data"]["items"][0]["id"] == user_id
    assert second_response.json()["data"]["items"][0]["roles"] == [{"code": "reader", "name": "只读测试角色"}]
    for invalid_query in ({"page": 0}, {"page_size": 101}):
        invalid_response = client.get("/users", headers=headers, params=invalid_query)
        assert invalid_response.status_code == 422
        assert invalid_response.json()["code"] == 1000
