"""缓存是可丢弃副本；验证真实回源、版本隔离和下一轮命中。"""
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.chat import MessageStatus
from app.core.config import get_settings
from app.schemas.chat import ContextMessage, TurnResult
from app.services import chat_context
from app.services.chat_turns import finish_turn, prepare_turn
from app.utils import cache
from tests.chat_support import chat_sessions, new_turn


class BrokenRedis:
    # 做什么：模拟 Redis 连接失败
    # 入参：缓存键
    # 返回：不返回，抛 Redis 连接异常
    def get(self, cache_key):
        raise RedisConnectionError("isolated cache outage")

    # 做什么：模拟写缓存时连接失败
    # 入参：缓存键、内容和过期时间
    # 返回：不返回，抛 Redis 连接异常
    def set(self, cache_key, content, ex):
        raise RedisConnectionError("isolated cache outage")


# 做什么：若命中测试错误地查询数据库则立即失败
# 入参：会话工厂、生成上下文和最大轮数
# 返回：不返回，抛断言错误
def unexpected_query(sessions, turn, max_rounds):
    raise AssertionError("cache hit must not rebuild history")


# 做什么：记录内存中的缓存键和值并保留过期时间
# 入参：可变字典、缓存键、内容和有效秒数
# 返回：无
def remember_cache(storage, cache_key, content, ttl_seconds):
    storage[cache_key] = content
    storage["ttl"] = ttl_seconds


# 做什么：确认完成后预热的缓存能被下一轮真实使用
# 入参：隔离会话工厂和替换工具
# 返回：无；命中时不能再次执行历史查询
def test_completed_turn_warms_cache_for_next_request(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_enabled", True)
    storage = {}
    monkeypatch.setattr(chat_context, "read_cache", storage.get)
    from functools import partial
    monkeypatch.setattr(chat_context, "write_cache", partial(remember_cache, storage))
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="第一次回答"))
    chat_context.warm_context(chat_sessions, turn)
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "继续")
    monkeypatch.setattr(chat_context, "query_context", unexpected_query)
    messages = chat_context.load_context(chat_sessions, next_turn)
    assert [message.content for message in messages] == ["测试问题", "第一次回答"]
    assert storage["ttl"] == get_settings().chat_cache_ttl_seconds


# 做什么：确认 Redis 故障时仍能拿到主存储中的历史
# 入参：隔离会话工厂和替换工具
# 返回：无；读写缓存故障均不能影响本次上下文
def test_redis_outage_falls_back_to_database(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "redis_enabled", True)
    monkeypatch.setattr(cache, "get_cache_client", BrokenRedis)
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="数据库回答"))
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "继续")
    messages = chat_context.load_context(chat_sessions, next_turn)
    assert messages[-1].content == "数据库回答"


# 做什么：返回结构合法但角色顺序错误的缓存
# 入参：缓存键
# 返回：需要被拒绝并回源的 JSON
def corrupt_cache(cache_key):
    return '[{"role":"assistant","content":"错误"},{"role":"user","content":"错误"}]'


# 做什么：确认损坏或角色错位的缓存不会注入模型上下文
# 入参：隔离会话工厂和替换工具
# 返回：无；首轮对话应回源为空历史
def test_corrupt_cache_is_rebuilt(chat_sessions, monkeypatch):
    monkeypatch.setattr(chat_context, "read_cache", corrupt_cache)
    turn = new_turn(chat_sessions)
    assert chat_context.load_context(chat_sessions, turn) == []


# 做什么：确认失败和中断轮次不会进入后续模型历史
# 入参：隔离会话工厂
# 返回：无；仅成功轮次可以成为上下文
@pytest.mark.parametrize("status", [MessageStatus.FAILED, MessageStatus.INTERRUPTED])
def test_unsuccessful_turns_are_excluded_from_context(chat_sessions, status):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=status, content="不完整内容"))
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "新的问题")
    assert chat_context.load_context(chat_sessions, next_turn) == []


# 做什么：确认上下文按完整轮次裁剪，不截断单条消息或留下半轮
# 入参：无
# 返回：无；过长最近轮次不能被当成完整上下文
def test_context_limits_preserve_complete_pairs():
    messages = [ContextMessage(role="user", content="旧问题"), ContextMessage(role="assistant", content="旧回答"),
                ContextMessage(role="user", content="新问题"), ContextMessage(role="assistant", content="新回答")]
    assert chat_context.bounded_context(messages, 1, 100) == messages[-2:]
    assert chat_context.bounded_context(messages, 10, 1) == []

