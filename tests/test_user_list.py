"""使用独立内存库验证用户列表分页及公开字段。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.security import hash_password
from app.db.base import Base
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.schemas.user import UserListQuery
from app.services.user import list_users
EXPECTED_ROLES = [{"code": "admin", "name": "管理员"}, {"code": "user", "name": "普通用户"}]

# 做什么：检查排序、分页、总数、越界空页和空库
# 入参：种子用户数量、页码、每页条数和预期用户编号
# 返回：无；分页或公开字段不符时断言失败，不访问真实库
@pytest.mark.parametrize("seed_count,page,page_size,expected_ids,expected_roles", [
    (3, 1, 2, [1, 2], [EXPECTED_ROLES, []]),
    (3, 2, 2, [3], [[]]),
    (3, 3, 2, [], []), (0, 1, 2, [], []),
])
def test_user_list_pagination(seed_count: int, page: int, page_size: int,
                              expected_ids: list[int], expected_roles: list[list[dict[str, str]]]) -> None:
    test_engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(test_engine)
        password_hash = hash_password("isolated-list-password")
        test_users = [
            User(id=3, username="third_user", password_hash=password_hash),
            User(id=1, username="first_user", password_hash=password_hash),
            User(id=2, username="second_user", password_hash=password_hash),
        ]
        with Session(test_engine) as seed_session, seed_session.begin():
            seed_session.add_all(test_users[:seed_count])
            seed_session.add(Role(id=1, code="admin", name="管理员"))
            seed_session.add(Role(id=2, code="user", name="普通用户"))
            seed_session.flush()
            seed_session.add_all([UserRole(user_id=1, role_id=1), UserRole(user_id=1, role_id=2)][:seed_count])
        with Session(test_engine) as query_session:
            page_result = list_users(query_session, UserListQuery(page=page, page_size=page_size))
        returned_ids = [user_item.id for user_item in page_result.items]
        assert returned_ids == expected_ids
        assert [user_item.model_dump()["roles"] for user_item in page_result.items] == expected_roles
        assert page_result.total == seed_count
        assert (page_result.page, page_result.page_size) == (page, page_size)
        serialized_result = page_result.model_dump_json()
        assert "password_hash" not in serialized_result and password_hash not in serialized_result
    finally:
        test_engine.dispose()
