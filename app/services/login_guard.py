"""登录失败锁定（内存版）。

规则：同一账号连续输错 5 次，锁 10 分钟；锁定期间即使密码正确也拒绝。
为什么放内存：阶段一只跑单进程，够用。阶段三迁 Redis，正好解决"重启就丢、多进程不共享"。

新手注意：像小区门禁，刷错 5 次后门锁 10 分钟，这 10 分钟里对的卡也刷不开。
"""

import logging
import threading
import time
from dataclasses import dataclass

from app.core.errors import AccountLockedError

logger = logging.getLogger(__name__)

# 连续失败多少次上锁；锁定多少秒。改这两个值等于改规则，写死在常量里方便一处调整
MAX_FAILURE_COUNT = 5
LOCK_DURATION_SECONDS = 600


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


# 做什么：判断这个账号现在是否处于锁定中
# 入参：username 登录名
# 返回：无；处于锁定中抛 AccountLockedError（429 / 1004）
# 为什么用 monotonic：它只往前走、不受系统改时间影响，算"还剩多久"更稳
def ensure_not_locked(username: str) -> None:
    with _guard_lock:
        state = _failure_states.get(username)
        still_locked = state is not None and state.locked_until > time.monotonic()

    if still_locked:
        raise AccountLockedError()


# 做什么：记一次登录失败，满了就上锁
# 入参：username 登录名
# 返回：无（副作用是改内存状态并记日志）
# 注意：锁定后把计数归零，等解锁后重新从 0 开始数，不会一解锁又立刻满
def record_failure(username: str) -> None:
    with _guard_lock:
        state = _failure_states.get(username, _FailureState())
        state.failure_count += 1
        current_count = state.failure_count
        reached_limit = current_count >= MAX_FAILURE_COUNT

        if reached_limit:
            state.locked_until = time.monotonic() + LOCK_DURATION_SECONDS
            state.failure_count = 0

        _failure_states[username] = state

    # 两条日志按发生顺序打：先说这次是第几次失败，再说因为到上限所以上锁
    logger.warning("登录失败 | username=%s | 连续次数=%d", username, current_count)
    if reached_limit:
        logger.warning("账号已锁定 | username=%s | 锁定秒数=%d", username, LOCK_DURATION_SECONDS)


# 做什么：登录成功后清掉这个账号的失败记录
# 入参：username 登录名
# 返回：无（副作用是删字典条目）
def clear_failures(username: str) -> None:
    with _guard_lock:
        _failure_states.pop(username, None)
