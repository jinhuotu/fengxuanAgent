"""使用独立内存库验证改密和失败时的数据保护。"""
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AccountDisabledError, BusinessError, CurrentPasswordIncorrectError, TokenInvalidError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import USER_STATUS_DISABLED, User
from app.services.password import change_password

ORIGINAL_PASSWORD = "original-test-password"
NEW_PASSWORD = "replacement-test-password"
WRONG_PASSWORD = "incorrect-test-password"

# 做什么：为每个用例创建独立的用户表和三个测试账号
# 入参：无，不连接项目 MySQL
# 返回：内存库会话；测试结束或异常时关闭会话并释放连接
@pytest.fixture
def password_session() -> Iterator[Session]:
    test_engine = create_engine("sqlite://")
    try:
        User.__table__.create(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        with test_sessions.begin() as test_session:
            password_hash = hash_password(ORIGINAL_PASSWORD)
            test_session.add(User(id=1, username="password_owner", password_hash=password_hash))
            test_session.add(User(id=2, username="other_user", password_hash=password_hash))
            test_session.add(User(id=3, username="disabled_user", password_hash=password_hash, status=USER_STATUS_DISABLED))
        with test_sessions() as test_session:
            yield test_session
    finally:
        test_engine.dispose()

# 做什么：验证本人改密、业务拒绝和其他账号的密码保护
# 入参：隔离会话、凭证用户编号、旧密码和预期异常
# 返回：无；行为不符时断言失败，不影响真实库
# 新手注意：参数化像拿同一张检查单，换四组输入分别检查。
@pytest.mark.parametrize("token_user_id,current_password,expected_error", [
    (1, ORIGINAL_PASSWORD, None),
    (1, WRONG_PASSWORD, CurrentPasswordIncorrectError),
    (3, ORIGINAL_PASSWORD, AccountDisabledError),
    (999, ORIGINAL_PASSWORD, TokenInvalidError),
])
def test_password_change_preserves_accounts(
    password_session: Session, token_user_id: int,
    current_password: str, expected_error: type[BusinessError] | None,
) -> None:
    if expected_error is None:
        change_password(password_session, create_access_token(token_user_id), current_password, NEW_PASSWORD)
    else:
        with pytest.raises(expected_error):
            change_password(password_session, create_access_token(token_user_id), current_password, NEW_PASSWORD)
    assert not password_session.in_transaction()
    with Session(password_session.bind) as verification_session:
        owner_hash = verification_session.get(User, 1).password_hash
        assert verify_password(NEW_PASSWORD, owner_hash) == (expected_error is None)
        assert verify_password(ORIGINAL_PASSWORD, owner_hash) == (expected_error is not None)
        assert verify_password(ORIGINAL_PASSWORD, verification_session.get(User, 2).password_hash)
