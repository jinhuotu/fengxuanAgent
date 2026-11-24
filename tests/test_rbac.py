"""独立验证角色追加绑定，不访问真实 MySQL。"""
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import BusinessError, RoleNotFoundError, UserNotFoundError
from app.core.security import hash_password
from app.db.base import Base
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import USER_STATUS_DISABLED, User
from app.services.rbac import bind_user_role, has_permission, list_permission_codes

# 做什么：创建独立内存库，准备一个已绑定普通角色的用户
# 入参：无，不连接项目数据库
# 返回：未开启事务的测试会话；结束或异常时关闭会话和连接
@pytest.fixture
def role_session() -> Iterator[Session]:
    test_engine = create_engine("sqlite://")
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

# 做什么：确认新增角色保留旧角色，重绑时不新增或替换关联
# 入参：role_session 提供已有普通角色的测试用户
# 返回：无；角色丢失、重复新增或关联被替换时断言失败
def test_binding_preserves_existing_roles(role_session: Session) -> None:
    assert bind_user_role(role_session, 1, "admin") is True
    with Session(role_session.bind) as verification_session:
        original_binding_id = verification_session.scalar(select(UserRole.id).where(UserRole.role_id == 2))
    assert bind_user_role(role_session, 1, "admin") is False
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
        bind_user_role(role_session, user_id, role_code)
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
        bind_user_role(role_session, 1, "admin")
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
    bind_user_role(role_session, 1, "admin")
    with role_session.begin():
        role_session.add(Permission(id=2, code="users:list", name="查看用户列表"))
        role_session.flush()
        role_session.add(RolePermission(role_id=1, permission_id=1))
        role_session.add(RolePermission(role_id=1, permission_id=2))
        if disabled:
            role_session.get(User, 1).status = USER_STATUS_DISABLED
    assert list_permission_codes(role_session, user_id) == expected
