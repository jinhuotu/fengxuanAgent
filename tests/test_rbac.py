"""隔离验证角色追加、撤销和权限查询。"""
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import BusinessError, RoleNotFoundError, UserNotFoundError
from app.core.security import create_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, User
from app.services import rbac as rbac_service
from app.services.rbac import bind_user_role, has_permission, list_permission_codes
from scripts import seed_rbac

# 做什么：创建每个用例独享的 SQLite 文件库，准备账号和角色
# 入参：pytest 提供的用例临时目录
# 返回：未开启事务的测试会话；结束或异常时关闭会话和连接
@pytest.fixture
def role_session(tmp_path: Path) -> Iterator[Session]:
    # 新手注意：数据库文件像一本共享账本，各连接独立记账，提交后的记录都能查到。
    database_url = URL.create("sqlite", database=str(tmp_path / "rbac.sqlite3"))
    test_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        with test_sessions.begin() as seed_session:
            seed_session.add(User(id=1, username="role_owner", password_hash=hash_password("isolated-test-password")))
            seed_session.add(User(id=2, username="unbound_admin", is_admin=True, password_hash=hash_password("isolated-test-password")))
            seed_session.add(Role(id=1, code="user", name="普通用户"))
            seed_session.add(Role(id=2, code="admin", name="管理员"))
            seed_session.add(Permission(id=1, code="users:create", name="新建用户"))
            seed_session.flush()
            seed_session.add(UserRole(id=1, user_id=1, role_id=1))
            seed_session.add(RolePermission(role_id=2, permission_id=1))
        with test_sessions() as test_session:
            yield test_session
    finally:
        test_engine.dispose()


# 做什么：让 HTTP 接口从同一个 SQLite 文件库取得独立会话
# 入参：隔离会话和替换会话工厂的 monkeypatch
# 返回：可调用真实接口的测试客户端，请求结束后关闭
@pytest.fixture
def rbac_client(role_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(database_session, "SessionLocal", sessionmaker(bind=role_session.bind))
    with TestClient(app) as client:
        yield client


# 做什么：给用户 1 授予独立的角色分配权限
# 入参：独立 SQLite 库的会话
# 返回：无；权限及其角色绑定保存到隔离库
@pytest.fixture
def grant_role_assignment(role_session: Session) -> None:
    with role_session.begin():
        role_session.add(Role(id=3, code="assigner", name="角色分配员"))
        role_session.add(Permission(id=2, code="users:assign_role", name="授予或撤销用户角色"))
        role_session.flush()
        role_session.add(RolePermission(role_id=3, permission_id=2))
        role_session.add(UserRole(user_id=1, role_id=3))


# 做什么：在隔离库给用户 1 授予切换账号状态的权限
# 入参：包含普通角色绑定的数据库会话
# 返回：无；users:disable 通过用户 1 的角色生效
@pytest.fixture
def grant_user_disable(role_session: Session) -> None:
    with role_session.begin():
        permission = Permission(code="users:disable", name="停用用户")
        role_session.add(permission)
        role_session.flush()
        role_session.add(RolePermission(role_id=1, permission_id=permission.id))


# 做什么：验证停用撤销旧 token，重复停用幂等，启用后可重新登录
# 入参：已有状态切换权限的隔离客户端和数据库会话
# 返回：无；接口响应或数据库版本不符时断言失败
def test_disable_and_enable_user_revokes_existing_token(
    role_session: Session, rbac_client: TestClient, grant_user_disable: None,
) -> None:
    operator_headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    login_body = {"username": "unbound_admin", "password": "isolated-test-password"}
    original_login = rbac_client.post("/auth/login", json=login_body)
    assert original_login.status_code == 200
    old_headers = {"Authorization": f"Bearer {original_login.json()['data']['access_token']}"}
    assert rbac_client.get("/auth/me", headers=old_headers).status_code == 200
    status_path = "/users/2/status"
    disable_body = {"status": USER_STATUS_DISABLED}
    disabled = rbac_client.patch(status_path, headers=operator_headers, json=disable_body)
    assert disabled.status_code == 200
    assert disabled.json()["data"] == {"changed": True, "status": USER_STATUS_DISABLED}
    blocked_login = rbac_client.post("/auth/login", json=login_body)
    assert (blocked_login.status_code, blocked_login.json()["code"]) == (403, 1003)
    revoked = rbac_client.get("/auth/me", headers=old_headers)
    assert (revoked.status_code, revoked.json()["code"]) == (401, 1002)
    repeated = rbac_client.patch(status_path, headers=operator_headers, json=disable_body)
    assert repeated.status_code == 200
    assert repeated.json()["data"] == {"changed": False, "status": USER_STATUS_DISABLED}
    enabled = rbac_client.patch(status_path, headers=operator_headers, json={"status": USER_STATUS_ACTIVE})
    assert enabled.status_code == 200
    assert enabled.json()["data"] == {"changed": True, "status": USER_STATUS_ACTIVE}
    still_revoked = rbac_client.get("/auth/me", headers=old_headers)
    assert (still_revoked.status_code, still_revoked.json()["code"]) == (401, 1002)
    assert rbac_client.post("/auth/login", json=login_body).status_code == 200
    assert role_session.scalar(select(User.token_version).where(User.id == 2)) == 1


# 做什么：验证无权限或操作自己时拒绝停用
# 入参：已配置权限的隔离客户端、操作者编号和预期错误码
# 返回：无；HTTP 状态或错误码不符时断言失败
@pytest.mark.parametrize("operator_id,error_code", [(2, 2001), (1, 2003)])
def test_user_status_denies_unauthorized_or_self(
    rbac_client: TestClient, grant_user_disable: None, operator_id: int, error_code: int,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(operator_id)}"}
    response = rbac_client.patch("/users/1/status", headers=headers, json={"status": USER_STATUS_DISABLED})
    assert (response.status_code, response.json()["code"]) == (403, error_code)


# 做什么：验证账号状态接口先检查权限，再校验非法状态值
# 入参：隔离客户端、操作者编号及预期 HTTP 状态和业务错误码
# 返回：无；无权限未返回 403/2001 或有权限未返回 422/1000 时失败
@pytest.mark.parametrize("operator_id,http_status,error_code", [
    pytest.param(2, 403, 2001, id="without-permission"),
    pytest.param(1, 422, 1000, id="with-permission"),
])
def test_user_status_rejects_unsupported_value(
    rbac_client: TestClient, grant_user_disable: None,
    operator_id: int, http_status: int, error_code: int,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(operator_id)}"}
    target_id = 1 if operator_id == 2 else 2
    response = rbac_client.patch(f"/users/{target_id}/status", headers=headers, json={"status": "pending"})
    assert (response.status_code, response.json()["code"]) == (http_status, error_code)


# 做什么：验证停用管理员前只统计其他启用的 admin 持有者
# 入参：隔离会话、接口客户端、另一名管理员状态和预期响应
# 返回：无；保护错误或目标状态不符时断言失败
@pytest.mark.parametrize("other_admin_status,http_status,error_code", [
    (USER_STATUS_DISABLED, 409, 2004),
    (USER_STATUS_ACTIVE, 200, 0),
])
def test_user_status_protects_last_active_admin(
    role_session: Session, rbac_client: TestClient, grant_user_disable: None,
    other_admin_status: str, http_status: int, error_code: int,
) -> None:
    with role_session.begin():
        role_session.add(User(
            id=3, username="other_admin", password_hash="unused-test-hash", status=other_admin_status
        ))
        role_session.flush()
        role_session.add(UserRole(user_id=2, role_id=2))
        role_session.add(UserRole(user_id=3, role_id=2))
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = rbac_client.patch("/users/2/status", headers=headers, json={"status": USER_STATUS_DISABLED})
    assert (response.status_code, response.json()["code"]) == (http_status, error_code)
    target_user = role_session.get(User, 2)
    if error_code == 2004:
        assert (target_user.status, target_user.token_version) == (USER_STATUS_ACTIVE, 0)
    else:
        assert (target_user.status, target_user.token_version) == (USER_STATUS_DISABLED, 1)


# 做什么：验证追加角色按权限码而非管理员身份判断，并检查边界响应
# 入参：已授予分配权限的隔离客户端、操作者、目标角色和预期响应
# 返回：无；权限、错误码或重复绑定结果不符时断言失败
@pytest.mark.parametrize("operator_id,user_id,role_code,http_status,error_code", [
    (1, 2, "user", 200, 0),
    (2, 1, "admin", 403, 2001),
    (1, 1, "admin", 403, 2003),
    (1, 999, "user", 404, 1007),
    (1, 2, "missing-role", 404, 2002),
])
def test_role_assignment_permission_and_boundaries(
    grant_role_assignment: None, rbac_client: TestClient, operator_id: int,
    user_id: int, role_code: str, http_status: int, error_code: int,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(operator_id)}"}
    binding_path = f"/users/{user_id}/roles"
    request_body = {"role_code": role_code}
    response = rbac_client.post(binding_path, headers=headers, json=request_body)
    assert (response.status_code, response.json()["code"]) == (http_status, error_code)
    if http_status == 200:
        assert response.json()["data"] == {"created": True}
        repeated = rbac_client.post(binding_path, headers=headers, json=request_body)
        assert repeated.status_code == 200
        assert repeated.json()["data"] == {"created": False}


# 做什么：验证撤销普通角色、重复撤销和原有其他角色保留
# 入参：已授予分配权限的隔离库和接口客户端
# 返回：无；撤销响应或剩余角色不符时断言失败
def test_role_revocation_is_idempotent_and_preserves_other_roles(
    role_session: Session, rbac_client: TestClient, grant_role_assignment: None,
) -> None:
    with role_session.begin():
        role_session.add(UserRole(user_id=2, role_id=1))
        role_session.add(UserRole(user_id=2, role_id=2))
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    first_response = rbac_client.delete("/users/2/roles/user", headers=headers)
    assert first_response.status_code == 200
    assert first_response.json()["data"] == {"removed": True}
    repeat_response = rbac_client.delete("/users/2/roles/user", headers=headers)
    assert repeat_response.status_code == 200
    assert repeat_response.json()["data"] == {"removed": False}
    remaining_roles_query = select(UserRole.role_id).where(UserRole.user_id == 2)
    assert role_session.scalars(remaining_roles_query).all() == [2]


# 做什么：验证缺少用户角色分配权限和撤销本人角色时拒绝请求
# 入参：隔离客户端、操作者编号和预期错误码
# 返回：无；错误码或 HTTP 状态不符时断言失败
@pytest.mark.parametrize("operator_id,error_code", [(2, 2001), (1, 2003)])
def test_role_revocation_denies_unauthorized_or_self(
    rbac_client: TestClient, grant_role_assignment: None, operator_id: int, error_code: int,
) -> None:
    headers = {"Authorization": f"Bearer {create_access_token(operator_id)}"}
    response = rbac_client.delete("/users/1/roles/user", headers=headers)
    assert (response.status_code, response.json()["code"]) == (403, error_code)


# 做什么：验证其他管理员启用时可撤销，只有停用的管理员剩余时阻止撤销
# 入参：隔离会话、客户端、另一名管理员状态及预期响应
# 返回：无；最后一名启用管理员保护或撤销结果不符时断言失败
@pytest.mark.parametrize("other_admin_status,http_status,error_code", [
    (USER_STATUS_DISABLED, 409, 2004),
    (USER_STATUS_ACTIVE, 200, 0),
])
def test_admin_revocation_requires_another_active_admin(
    role_session: Session, rbac_client: TestClient, grant_role_assignment: None,
    other_admin_status: str, http_status: int, error_code: int,
) -> None:
    with role_session.begin():
        role_session.add(User(id=3, username="other_admin", password_hash="unused-test-hash", status=other_admin_status))
        role_session.flush()
        role_session.add(UserRole(user_id=2, role_id=2))
        role_session.add(UserRole(user_id=3, role_id=2))
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    response = rbac_client.delete("/users/2/roles/admin", headers=headers)
    assert (response.status_code, response.json()["code"]) == (http_status, error_code)
    if error_code == 2004:
        assert response.json()["message"] == "系统至少保留一名管理员"
    else:
        assert response.json()["data"] == {"removed": True}
    target_binding = select(UserRole.id).where(UserRole.user_id == 2, UserRole.role_id == 2)
    assert (role_session.scalar(target_binding) is not None) == (error_code == 2004)


# 做什么：确认角色清单需要 users:list 权限，且跨批次返回完整的公开字段
# 入参：隔离会话、接口客户端和临时调整查询批量大小的 monkeypatch
# 返回：无；鉴权响应或返回项不符时断言失败
def test_role_catalog_requires_list_permission(
    role_session: Session, rbac_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rbac_service, "ROLE_LIST_BATCH_SIZE", 1)
    with role_session.begin():
        role_session.add(Permission(id=2, code="users:list", name="查看用户列表"))
        role_session.flush()
        role_session.add(RolePermission(role_id=2, permission_id=2))
        role_session.add(UserRole(user_id=2, role_id=2))

    assert rbac_client.get("/roles").status_code == 401
    user_headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    forbidden = rbac_client.get("/roles", headers=user_headers)
    assert (forbidden.status_code, forbidden.json()["code"]) == (403, 2001)
    admin_headers = {"Authorization": f"Bearer {create_access_token(2)}"}
    response = rbac_client.get("/roles", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["data"] == {"items": [
        {"code": "admin", "name": "管理员"},
        {"code": "user", "name": "普通用户"},
    ]}


# 做什么：对已有角色和权限数据连续执行两次真实种子脚本
# 入参：隔离会话和替换脚本数据库连接的 monkeypatch
# 返回：无；权限数量变化或新权限未挂到 admin 时断言失败
def test_seed_rbac_is_idempotent(role_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    test_sessions = sessionmaker(bind=role_session.bind)
    monkeypatch.setattr(seed_rbac, "SessionLocal", test_sessions)
    monkeypatch.setattr(seed_rbac, "setup_logging", lambda environment: None)
    permission_count_query = select(func.count()).select_from(Permission)
    seed_rbac.main()
    with test_sessions() as first_session:
        first_permission_count = first_session.scalar(permission_count_query)
    seed_rbac.main()
    with test_sessions.begin() as second_session:
        assert second_session.scalar(permission_count_query) == first_permission_count == 4
        second_session.add(UserRole(user_id=2, role_id=2))
    assert has_permission(role_session, 2, "users:assign_role")

# 做什么：确认新增角色保留旧角色，重绑时不新增或替换关联
# 入参：role_session 提供已有普通角色的测试用户
# 返回：无；角色丢失、重复新增或关联被替换时断言失败
def test_binding_preserves_existing_roles(role_session: Session) -> None:
    assert bind_user_role(role_session, 1, "admin", operator_id=2) is True
    with Session(role_session.bind) as verification_session:
        original_binding_id = verification_session.scalar(select(UserRole.id).where(UserRole.role_id == 2))
    assert bind_user_role(role_session, 1, "admin", operator_id=2) is False
    assert not role_session.in_transaction()
    with Session(role_session.bind) as verification_session:
        bindings = verification_session.scalars(select(UserRole).order_by(UserRole.role_id)).all()
        assert [(binding.id, binding.role_id) for binding in bindings] == [(1, 1), (original_binding_id, 2)]

# 做什么：确认目标不存在时拒绝绑定，并保持已有关系不变
# 入参：隔离会话、用户编号、角色标识和预期异常
# 返回：无；未拒绝或失败后多写关联时断言失败
@pytest.mark.parametrize("user_id,role_code,expected_error", [
    (999, "admin", UserNotFoundError), (1, "missing-role", RoleNotFoundError),
])
def test_missing_binding_target_does_not_write(
    role_session: Session, user_id: int, role_code: str, expected_error: type[BusinessError],
) -> None:
    with pytest.raises(expected_error):
        bind_user_role(role_session, user_id, role_code, operator_id=2)
    assert not role_session.in_transaction()
    assert role_session.scalars(select(UserRole.role_id)).all() == [1]


# 做什么：验证多角色取并集，停用、无角色及缺少权限时拒绝
# 入参：隔离会话、查询用户、权限码、绑定/停用条件及预期结果
# 返回：无；权限结果不符时断言失败，不修改真实授权
@pytest.mark.parametrize("user_id,permission_code,bind_admin,disable_user,expected", [
    (1, "users:create", False, False, False),
    (1, "users:create", True, False, True),
    (1, "users:disable", True, False, False),
    (1, "users:create", True, True, False),
    (2, "users:create", False, False, False),
    (999, "users:create", False, False, False),
])
def test_permission_lookup(
    role_session: Session, user_id: int, permission_code: str,
    bind_admin: bool, disable_user: bool, expected: bool,
) -> None:
    if bind_admin:
        bind_user_role(role_session, 1, "admin", operator_id=2)
    if disable_user:
        with role_session.begin():
            role_session.get(User, 1).status = USER_STATUS_DISABLED
    assert has_permission(role_session, user_id, permission_code) is expected


# 做什么：验证权限清单并集、去重、排序及空权限行为
# 入参：隔离会话、用户编号、停用标记与预期权限码
# 返回：无；清单不符时断言失败，不操作真实数据库
@pytest.mark.parametrize("user_id,disabled,expected", [
    (1, False, ["users:create", "users:list"]), (1, True, []),
    (2, False, []), (999, False, []),
])
def test_permission_codes(role_session: Session, user_id: int, disabled: bool, expected: list[str]) -> None:
    bind_user_role(role_session, 1, "admin", operator_id=2)
    with role_session.begin():
        role_session.add(Permission(id=2, code="users:list", name="查看用户列表"))
        role_session.flush()
        role_session.add(RolePermission(role_id=1, permission_id=1))
        role_session.add(RolePermission(role_id=1, permission_id=2))
        if disabled:
            role_session.get(User, 1).status = USER_STATUS_DISABLED
    assert list_permission_codes(role_session, user_id) == expected
