"""登录锁定后端选择与故障降级的隔离测试。"""

from unittest.mock import Mock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import get_settings
from app.core.errors import AccountLockedError
from app.services import login_guard
from app.utils import cache


# 做什么：让每个用例使用独立的进程内状态
# 入参：monkeypatch 替换状态字典
# 返回：无；用例结束时自动恢复原字典
@pytest.fixture(autouse=True)
def isolated_memory_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_guard, "_failure_states", {})


# 做什么：启用 Redis 分支并阻止隔离用例连接真实服务器
# 入参：monkeypatch 替换配置和客户端
# 返回：执行业务操作时使用的模拟客户端
@pytest.fixture
def redis_client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    monkeypatch.setattr(get_settings(), "redis_enabled", True)
    monkeypatch.setattr(cache, "get_cache_client", Mock(return_value=client))
    return client


# 做什么：验证配置启用 Redis 时，只有 Redis 记录的锁也能挡住登录
# 入参：模拟 Redis 客户端和锁定剩余秒数
# 返回：无；锁未生效或 Redis 键错误时断言失败
@pytest.mark.parametrize("lock_ttl", [0, login_guard.LOCK_DURATION_SECONDS])
def test_redis_lock_is_visible_without_local_state(redis_client: Mock, lock_ttl: int) -> None:
    username = "redis_locked_test"
    redis_client.ttl.return_value = lock_ttl

    with pytest.raises(AccountLockedError):
        login_guard.ensure_not_locked(username)
    redis_client.ttl.assert_called_once_with(f"fengxuan:login:{username}:locked")


# 做什么：缺锁是正常结果，计数、阈值和两个 TTL 交给同一条 Redis 命令
# 入参：模拟 Redis 客户端和日志捕获
# 返回：无；若回退内存、出现错误日志或命令参数不符则断言失败
def test_missing_redis_lock_does_not_trigger_fallback(
    redis_client: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    username = "redis_missing_lock_test"
    redis_client.ttl.return_value = cache.REDIS_KEY_MISSING_TTL
    redis_client.eval.return_value = 1

    login_guard.ensure_not_locked(username)
    login_guard.record_failure(username)

    assert not login_guard._failure_states
    assert "Redis 登录保护故障" not in caplog.text
    assert redis_client.eval.call_args.args[1:] == (
        2, f"fengxuan:login:{username}:failures", f"fengxuan:login:{username}:locked", 5, 600, 600
    )


# 做什么：Redis 读写异常时验证内存锁定和故障日志
# 入参：模拟故障客户端和日志捕获
# 返回：无；不能按内存规则锁定或故障未记为 ERROR 时断言失败
def test_redis_outage_falls_back_to_memory(
    redis_client: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    username = "redis_outage_test"
    redis_client.ttl.side_effect = RedisConnectionError("模拟连接中断")
    redis_client.eval.side_effect = RedisConnectionError("模拟连接中断")

    for failure in range(login_guard.MAX_FAILURE_COUNT):
        login_guard.ensure_not_locked(username)
        login_guard.record_failure(username)

    # Redis 恢复不会提前清掉本进程已经建立的锁。
    redis_client.ttl.side_effect = None
    redis_client.ttl.return_value = cache.REDIS_KEY_MISSING_TTL
    with pytest.raises(AccountLockedError):
        login_guard.ensure_not_locked(username)
    redis_errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(redis_errors) == 2 * login_guard.MAX_FAILURE_COUNT
    assert all(username in record.getMessage() for record in redis_errors)


# 做什么：确保并发请求刚建立 Redis 锁时，已验密的请求不能清锁
# 入参：模拟 Redis 客户端，返回清理被锁拦截的结果
# 返回：无；如果没有抛出账号锁定错误就断言失败
def test_success_does_not_clear_a_concurrent_redis_lock(redis_client: Mock) -> None:
    redis_client.eval.return_value = 0
    with pytest.raises(AccountLockedError):
        login_guard.clear_failures("redis_concurrent_lock_test")
