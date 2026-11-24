"""登录失败锁定，使用 Redis 共享状态，故障时回退进程内存。

规则：同一账号连续输错 5 次，锁 10 分钟；锁定期间即使密码正确也拒绝。
新手注意：像小区门禁，刷错 5 次后门锁 10 分钟，这 10 分钟里对的卡也刷不开。
"""

import logging
import threading
import time
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import AccountLockedError
from app.utils import cache

logger = logging.getLogger(__name__)

# 连续失败多少次上锁；锁定多少秒。改这两个值等于改规则，写死在常量里方便一处调整
MAX_FAILURE_COUNT = 5
LOCK_DURATION_SECONDS = 600
FAILURE_COUNT_TTL_SECONDS = 600


# 做什么：一个账号当前的失败状态
# 入参：无（由下方字典持有）
# 返回：无（普通数据容器）
@dataclass
class _FailureState:
    failure_count: int = 0
    locked_until: float = 0.0


# 键是用户名，值是失败状态。进程重启即清空，这是内存方案的已知代价
_failure_states: dict[str, _FailureState] = {}

# 同步接口跑在线程池里，多个请求可能同时改同一个计数；
# 不加锁会出现"两个请求都读到 4，都写 5"这种少计一次的情况
_guard_lock = threading.Lock()


# 做什么：判断内存状态中的锁是否有效
# 入参：可能不存在的内存状态
# 返回：仍处于锁定中为 True，否则为 False
# 为什么用 monotonic：计时只往前走，不受系统时钟调整影响。
def _memory_lock_is_active(state: _FailureState | None) -> bool:
    return state is not None and state.locked_until > time.monotonic()


# 做什么：记录 Redis 故障和内存降级
# 入参：登录名、当前操作和 Redis 异常
# 返回：无；日志只记录异常类型，不记录连接信息和认证数据
def _log_redis_failure(username: str, operation: str, error: RedisError) -> None:
    logger.error(
        "Redis 登录保护故障，转用内存 | username=%r | operation=%s | error_type=%s",
        username, operation, type(error).__name__,
    )


# 做什么：判断这个账号是否在内存或 Redis 中锁定
# 入参：username 登录名
# 返回：无；处于锁定中抛 AccountLockedError（429 / 1004）
def ensure_not_locked(username: str) -> None:
    # Redis 恢复后仍尊重当前进程在故障期间建立的锁，直到它自然过期。
    with _guard_lock:
        if _memory_lock_is_active(_failure_states.get(username)):
            raise AccountLockedError()
    if not get_settings().redis_enabled:
        return
    try:
        lock_ttl = cache.read_login_lock_ttl(username)
    except RedisError as error:
        _log_redis_failure(username, "查锁", error)
        return
    if lock_ttl is not None:
        raise AccountLockedError()


# 做什么：按原内存规则记一次失败并在达到上限时锁定
# 入参：登录名
# 返回：本次计数，已有活动锁返回 0
def _record_memory_failure(username: str) -> int:
    with _guard_lock:
        state = _failure_states.get(username, _FailureState())
        if _memory_lock_is_active(state):
            return 0
        state.failure_count += 1
        current_count = state.failure_count
        if current_count >= MAX_FAILURE_COUNT:
            state.locked_until = time.monotonic() + LOCK_DURATION_SECONDS
            state.failure_count = 0
        _failure_states[username] = state
        return current_count


# 做什么：用配置指定的后端记一次登录失败
# 入参：登录名
# 返回：无；已有活动锁时抛 AccountLockedError，Redis 失败时回退内存
def record_failure(username: str) -> None:
    current_count = None
    if get_settings().redis_enabled:
        try:
            current_count = cache.record_login_failure(
                username, MAX_FAILURE_COUNT, FAILURE_COUNT_TTL_SECONDS, LOCK_DURATION_SECONDS
            )
        except RedisError as error:
            _log_redis_failure(username, "记失败", error)
    if current_count is None:
        current_count = _record_memory_failure(username)
    if current_count == 0:
        raise AccountLockedError()
    logger.warning("登录失败 | username=%s | 连续次数=%d", username, current_count)
    if current_count >= MAX_FAILURE_COUNT:
        logger.warning("账号已锁定 | username=%s | 锁定秒数=%d", username, LOCK_DURATION_SECONDS)


# 做什么：密码验证通过时确认没有并发锁定，然后清空失败记录
# 入参：username 登录名
# 返回：无；发现新锁时抛 AccountLockedError，Redis 失败时只清内存
def clear_failures(username: str) -> None:
    can_clear = True
    if get_settings().redis_enabled:
        try:
            can_clear = cache.clear_login_failures(username)
        except RedisError as error:
            _log_redis_failure(username, "清计数", error)
    if not can_clear:
        raise AccountLockedError()
    with _guard_lock:
        if _memory_lock_is_active(_failure_states.get(username)):
            raise AccountLockedError()
        _failure_states.pop(username, None)
