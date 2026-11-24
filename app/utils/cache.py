"""Redis 连接、对话缓存和登录保护操作。"""
import logging
from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
MAX_CACHE_CONNECTIONS = 20
LOGIN_KEY_PREFIX = "fengxuan:login:"
REDIS_KEY_MISSING_TTL = -2

# 新手注意：Lua 像让 Redis 一次办理整笔业务，中间不会插进其他请求。
_RECORD_LOGIN_FAILURE = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return 0
end
local failure_count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[2])
if failure_count >= tonumber(ARGV[1]) then
    redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
    redis.call('DEL', KEYS[1])
end
return failure_count
"""

# 成功请求可能在另一请求上锁前开始验密，清计数时要重新确认有无活动锁。
_CLEAR_LOGIN_FAILURES = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


# 做什么：创建可复用的 Redis 连接池，不在创建时连接服务器
# 入参：无，连接参数来自统一配置
# 返回：Redis 客户端；实际连接错误在读取或写入时处理
@lru_cache
def get_cache_client() -> Redis:
    settings = get_settings()
    return Redis(
        host=settings.redis_host, port=settings.redis_port, db=settings.redis_database,
        password=settings.redis_password.get_secret_value() or None,
        socket_timeout=settings.redis_timeout_seconds,
        socket_connect_timeout=settings.redis_timeout_seconds, decode_responses=True,
        retry_on_timeout=False, max_connections=MAX_CACHE_CONNECTIONS,
    )


# 做什么：尝试读取可重建的缓存
# 入参：缓存键，其中包含业务编号
# 返回：命中时返回字符串；未命中、停用或 Redis 故障返回 None
def read_cache(cache_key: str) -> str | None:
    if not get_settings().redis_enabled:
        return None
    try:
        return get_cache_client().get(cache_key)
    except RedisError:
        logger.warning("缓存读取失败，回源数据库 | cache_key=%s", cache_key)
        return None


# 做什么：写入带过期时间的可重建缓存
# 入参：缓存键、序列化内容和有效秒数
# 返回：无；Redis 故障只记录安全警告，不影响数据库结果
def write_cache(cache_key: str, content: str, ttl_seconds: int) -> None:
    if not get_settings().redis_enabled:
        return
    try:
        get_cache_client().set(cache_key, content, ex=ttl_seconds)
    except RedisError:
        logger.warning("缓存写入失败，保留数据库结果 | cache_key=%s", cache_key)


# 做什么：为一个登录名生成失败计数键和锁定键
# 入参：登录名
# 返回：按计数键、锁定键顺序排列的两个字符串
def _login_keys(username: str) -> tuple[str, str]:
    account_prefix = f"{LOGIN_KEY_PREFIX}{username}"
    return f"{account_prefix}:failures", f"{account_prefix}:locked"


# 做什么：读取登录锁剩余时间，让调用方区分缺键和 Redis 故障
# 入参：登录名
# 返回：存在时返回原始 TTL，缺键返回 None，Redis 故障抛 RedisError
def read_login_lock_ttl(username: str) -> int | None:
    _, lock_key = _login_keys(username)
    ttl_seconds = get_cache_client().ttl(lock_key)
    if ttl_seconds == REDIS_KEY_MISSING_TTL:
        return None
    return ttl_seconds


# 做什么：原子记录失败，达到上限时写锁并删除计数
# 入参：登录名、失败上限、计数空闲秒数和锁定秒数
# 返回：本次计数，已锁定返回 0，Redis 故障抛 RedisError
def record_login_failure(
    username: str, failure_limit: int, count_ttl_seconds: int, lock_ttl_seconds: int
) -> int:
    count_key, lock_key = _login_keys(username)
    return get_cache_client().eval(
        _RECORD_LOGIN_FAILURE, 2, count_key, lock_key,
        failure_limit, count_ttl_seconds, lock_ttl_seconds,
    )


# 做什么：登录成功时原子确认未锁定并清除失败计数
# 入参：登录名
# 返回：允许登录返回 True，已锁定返回 False，Redis 故障抛 RedisError
def clear_login_failures(username: str) -> bool:
    count_key, lock_key = _login_keys(username)
    return bool(get_cache_client().eval(_CLEAR_LOGIN_FAILURES, 2, count_key, lock_key))

