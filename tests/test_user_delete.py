"""隔离验证用户逻辑删除：不删行、不能登录、列表消失、保护规则。"""
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import CannotModifySelfError
from app.core.security import create_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import IS_DELETED, IS_NOT_DELETED, USER_STATUS_ACTIVE, User
from app.services.user import delete_user


# 做什么：创建每个用例独享的 SQLite 文件库，准备操作者、目标和角色
# 入参：pytest 提供的用例临时目录
# 返回：未开启事务的测试会话；结束或异常时关闭会话和连接
@pytest.fixture
def delete_session(tmp_path: Path) -> Iterator[Session]:
    # 新手注意：数据库文件像一本共享账本，各连接独立记账，提交后的记录都能查到。
    database_url = URL.create("sqlite", database=str(tmp_path / "user_delete.sqlite3"))
    test_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        with test_sessions.begin() as seed_session:
            # 用户 1 是操作者，挂普通角色（角色 1）；用户 2 是删除目标，暂不挂任何角色
            seed_session.add(User(id=1, username="operator", password_hash=hash_password("isolated-test-password")))
            seed_session.add(User(id=2, username="delete_target", password_hash=hash_password("isolated-test-password")))
            seed_session.add(Role(id=1, code="user", name="普通用户"))
            seed_session.add(Role(id=2, code="admin", name="管理员"))
            seed_session.flush()
            seed_session.add(UserRole(id=1, user_id=1, role_id=1))
        with test_sessions() as test_session:
            yield test_session
    finally:
        test_engine.dispose()


# 做什么：让 HTTP 接口从同一个 SQLite 文件库取得独立会话
# 入参：隔离会话和替换会话工厂的 monkeypatch
# 返回：可调用真实接口的测试客户端，请求结束后关闭
@pytest.fixture
def delete_client(delete_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(database_session, "SessionLocal", sessionmaker(bind=delete_session.bind))
    with TestClient(app) as client:
        yield client


# 做什么：在隔离库给用户 1 授予删除用户的权限
# 入参：包含普通角色绑定的数据库会话
# 返回：无；users:delete 通过用户 1 的角色生效
@pytest.fixture
def grant_user_delete(delete_session: Session) -> None:
    with delete_session.begin():
        permission = Permission(code="users:delete", name="删除用户")
        delete_session.add(permission)
        delete_session.flush()
        delete_session.add(RolePermission(role_id=1, permission_id=permission.id))


# 做什么：在隔离库给用户 1 追加查看用户列表的权限
# 入参：已经授予删除权限的数据库会话
# 返回：无；users:list 通过用户 1 的角色生效
@pytest.fixture
def grant_user_list(grant_user_delete: None, delete_session: Session) -> None:
    with delete_session.begin():
        permission = Permission(code="users:list", name="查看用户列表")
        delete_session.add(permission)
        delete_session.flush()
        delete_session.add(RolePermission(role_id=1, permission_id=permission.id))


# 做什么：把目标用户 2 变成一名启用的管理员，可选择再准备另一名管理员
# 入参：隔离会话和是否另有其他启用的管理员
# 返回：无；角色绑定保存到隔离库
def make_target_admin(delete_session: Session, *, with_other_admin: bool) -> None:
    with delete_session.begin():
        delete_session.add(UserRole(user_id=2, role_id=2))
        if with_other_admin:
            delete_session.add(User(id=3, username="other_admin", password_hash="unused-test-hash"))
            delete_session.flush()
            delete_session.add(UserRole(user_id=3, role_id=2))


# 做什么：读取目标用户 2 的删除标记、状态和令牌版本
# 入参：隔离会话
# 返回：依次为是否已删除、账号状态、令牌版本
def read_target_state(delete_session: Session) -> tuple[bool, str, int]:
    target_user = delete_session.get(User, 2)
    assert target_user is not None
    return (target_user.is_deleted == IS_DELETED, target_user.status, target_user.token_version)


# 做什么：验证删除后行还在、旧凭证失效、不能登录、列表不再出现
# 入参：隔离会话、已授予删除和列表权限的客户端
# 返回：无；响应、登录结果或数据库行不符时断言失败
def test_soft_delete_keeps_row_and_blocks_login(
    delete_session: Session, delete_client: TestClient, grant_user_list: None,
) -> None:
    operator_headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    login_body = {"username": "delete_target", "password": "isolated-test-password"}
    original_login = delete_client.post("/auth/login", json=login_body)
    assert original_login.status_code == 200
    old_headers = {"Authorization": f"Bearer {original_login.json()['data']['access_token']}"}

    deleted = delete_client.delete("/users/2", headers=operator_headers)

    assert (deleted.status_code, deleted.json()["data"]) == (200, {"deleted_user_id": 2})
    revoked = delete_client.get("/auth/me", headers=old_headers)
    assert (revoked.status_code, revoked.json()["code"]) == (401, 1002)
    blocked_login = delete_client.post("/auth/login", json=login_body)
    assert (blocked_login.status_code, blocked_login.json()["code"]) == (403, 1003)
    user_list = delete_client.get("/users", headers=operator_headers).json()["data"]
    assert (user_list["total"], [item["id"] for item in user_list["items"]]) == (1, [1])
    # 新手注意：逻辑删除像给档案盖上“作废”章后归档，纸还在柜子里，只是不再对外提供。
    assert read_target_state(delete_session) == (True, USER_STATUS_ACTIVE, 1)


# 做什么：在隔离库给用户 1 追加切换账号状态的权限，用于验证已删除账号不能被启用
# 入参：已经授予删除权限的数据库会话
# 返回：无；users:disable 通过用户 1 的角色生效
@pytest.fixture
def grant_user_disable(grant_user_delete: None, delete_session: Session) -> None:
    with delete_session.begin():
        permission = Permission(code="users:disable", name="停用用户")
        delete_session.add(permission)
        delete_session.flush()
        delete_session.add(RolePermission(role_id=1, permission_id=permission.id))


# 做什么：验证已删除账号对外表现为不存在：不能改状态、不能重复删除
# 入参：隔离会话、已授予删除、列表和停用权限的客户端
# 返回：无；已删除账号被重新启用或重复删除未返回 404 时断言失败
def test_deleted_user_behaves_as_missing(
    delete_session: Session, delete_client: TestClient, grant_user_list: None, grant_user_disable: None,
) -> None:
    operator_headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    assert delete_client.delete("/users/2", headers=operator_headers).status_code == 200

    status_path = "/users/2/status"
    enabled = delete_client.patch(status_path, headers=operator_headers, json={"status": USER_STATUS_ACTIVE})
    assert (enabled.status_code, enabled.json()["code"]) == (404, 1007)
    repeated = delete_client.delete("/users/2", headers=operator_headers)
    assert (repeated.status_code, repeated.json()["code"]) == (404, 1007)
    assert read_target_state(delete_session) == (True, USER_STATUS_ACTIVE, 1)


# 做什么：验证没有删除权限的用户 2 被拒绝，目标保持未删除
# 入参：隔离会话、客户端和已授予用户 1 删除权限的夹具
# 返回：无；未返回 403/2001 或目标被标记删除时断言失败
def test_delete_denies_without_permission(
    delete_session: Session, delete_client: TestClient, grant_user_delete: None,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(2)}"}
    response = delete_client.delete("/users/1", headers=headers)
    assert (response.status_code, response.json()["code"]) == (403, 2001)
    assert read_target_state(delete_session) == (False, USER_STATUS_ACTIVE, 0)


# 做什么：验证有权限时删除本人被拒绝
# 入参：隔离会话、已授权的客户端
# 返回：未返回 403/2003 或本人被标记删除时断言失败
def test_delete_denies_self(delete_session: Session, delete_client: TestClient, grant_user_delete: None) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = delete_client.delete("/users/1", headers=headers)
    assert (response.status_code, response.json()["code"]) == (403, 2003)
    operator_user = delete_session.get(User, 1)
    assert operator_user is not None and operator_user.is_deleted == IS_NOT_DELETED


# 做什么：验证目标是最后一名启用的管理员时拒绝删除，已有另一名时放行
# 入参：隔离会话、已授权的客户端、是否有另一名管理员和预期响应
# 返回：无；保护未生效或删除结果不符时断言失败
@pytest.mark.parametrize("with_other_admin,http_status,error_code", [
    pytest.param(False, 409, 2004, id="last-active-admin"),
    pytest.param(True, 200, 0, id="another-admin-remains"),
])
def test_delete_protects_last_active_admin(
    delete_session: Session, delete_client: TestClient, grant_user_delete: None,
    with_other_admin: bool, http_status: int, error_code: int,
) -> None:
    make_target_admin(delete_session, with_other_admin=with_other_admin)
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = delete_client.delete("/users/2", headers=headers)
    assert (response.status_code, response.json()["code"]) == (http_status, error_code)
    assert read_target_state(delete_session)[0] is with_other_admin


# 做什么：验证目标是已停用的管理员时不触发最后管理员保护，可以删除
# 入参：隔离会话、已授权的客户端
# 返回：未成功删除或删除后状态被改动时断言失败
def test_delete_allows_removing_disabled_admin(
    delete_session: Session, delete_client: TestClient, grant_user_delete: None,
) -> None:
    with delete_session.begin():
        target_user = delete_session.get(User, 2)
        assert target_user is not None
        target_user.status = "disabled"
        delete_session.add(UserRole(user_id=2, role_id=2))
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = delete_client.delete("/users/2", headers=headers)
    assert response.status_code == 200
    assert read_target_state(delete_session) == (True, "disabled", 1)


# 做什么：验证目标不存在时接口返回 404，且不改动任何用户
# 入参：隔离会话、已授权的客户端
# 返回：无；响应或数据库状态不符时断言失败
def test_delete_missing_user_returns_not_found(
    delete_session: Session, delete_client: TestClient, grant_user_delete: None,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = delete_client.delete("/users/999", headers=headers)
    assert (response.status_code, response.json()["code"]) == (404, 1007)
    deleted_count = select(User.id).where(User.is_deleted == IS_DELETED)
    assert delete_session.scalars(deleted_count).all() == []


# 做什么：验证服务层直接调用时同样拒绝本人，且没有留下未结束的事务
# 入参：隔离会话
# 返回：未抛业务异常或留下未结束的事务时断言失败
def test_delete_user_service_rejects_self(delete_session: Session) -> None:
    with pytest.raises(CannotModifySelfError):
        delete_user(delete_session, operator_id=1, user_id=1)
    assert not delete_session.in_transaction()
