"""上下文完整请求预算回归；不调用真实模型。"""
from uuid import uuid4
from functools import partial
import asyncio
import logging

import pytest
from sqlalchemy import func, select

from app.core.chat import MessageStatus
from app.schemas.chat import ContextCandidates, ContextMessage, TurnResult
from app.core.config import get_settings
from app.core.chat_errors import ModelBudgetConfigurationError, ModelContextBudgetError
from app.llm import client as model_client
from app.llm.tokens import effective_input_budget, ensure_request_budget, estimate_message_tokens, estimate_request_tokens, estimate_text_tokens
from app.models.chat import ChatMessage, ChatSession
from app.services.chat_budget import plan_context
from app.services import chat_context
from app.services import chat_stream
from app.services.chat_history import create_session
from app.services.chat_turns import finish_turn, prepare_turn
from tests.chat_support import chat_sessions, new_turn
from tests.chat_support import event_items
from tests.test_chat_cache import remember_cache, unexpected_query
from tests.test_chat_stream import collect_stream, completed_model


# 做什么：复现最近一轮过长被全部省略时没有任何通知
# 入参：隔离工厂和替换工具
# 返回：无；无法得知最近历史未携带时失败
def test_oversized_latest_round_is_reported(chat_sessions, monkeypatch):
    storage = {}
    monkeypatch.setattr(chat_context, "read_cache", storage.get)
    monkeypatch.setattr(chat_context, "write_cache", partial(remember_cache, storage))
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="长" * 13000))
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "继续上一轮")
    candidates = chat_context.load_context(chat_sessions, next_turn)
    assert candidates == []
    monkeypatch.setattr(chat_context, "query_context", unexpected_query)
    plan = chat_context.load_context_plan(chat_sessions, next_turn)
    assert plan.messages == []
    assert plan.history_truncated
    assert plan.latest_round_omitted


# 做什么：确认有效输入预算同时受输出、安全空间和应用限额约束
# 入参：配置替换工具
# 返回：无；只限制历史或忘记输出预留时失败
def test_effective_budget_reserves_output_and_safety(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_context_window_tokens", 400)
    monkeypatch.setattr(settings, "llm_max_output_tokens", 100)
    monkeypatch.setattr(settings, "chat_context_safety_tokens", 20)
    monkeypatch.setattr(settings, "chat_input_token_budget", 1000)
    assert effective_input_budget() == 280
    monkeypatch.setattr(settings, "chat_input_token_budget", 200)
    assert effective_input_budget() == 200
    monkeypatch.setattr(settings, "llm_context_window_tokens", 120)
    with pytest.raises(ModelBudgetConfigurationError):
        effective_input_budget()


# 做什么：确认中文、代码和消息结构都计入估算，不宣称精确分词
# 入参：无
# 返回：无；估算遗漏正文或结构时失败
def test_estimator_counts_utf8_and_message_overhead():
    content = "中文\n```python\nprint('hello')\n```"
    assert estimate_text_tokens(content) == len(content.encode("utf-8"))
    assert estimate_message_tokens(content) > estimate_text_tokens(content)
    assert estimate_request_tokens("系统", [], content) > estimate_message_tokens(content)


# 做什么：确认边界恰好容纳完整最近轮次，差一个单位也不会截断正文
# 入参：配置替换工具
# 返回：无；拆开轮次、删掉当前问题预算或边界错误时失败
def test_selection_keeps_complete_recent_pair_at_exact_boundary(monkeypatch):
    messages = [ContextMessage(role="user", content="旧问题"), ContextMessage(role="assistant", content="旧回答"),
                ContextMessage(role="user", content="新问题"), ContextMessage(role="assistant", content="新回答")]
    candidates = ContextCandidates(messages=messages, history_truncated=False, latest_round_omitted=False)
    boundary = estimate_request_tokens("系统", messages[-2:], "完整当前问题")
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", boundary)
    plan = plan_context(candidates, "系统", "完整当前问题")
    assert plan.messages == messages[-2:]
    assert plan.estimated_input_tokens == boundary
    assert plan.history_truncated and not plan.latest_round_omitted
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", boundary - 1)
    plan = plan_context(candidates, "系统", "完整当前问题")
    assert plan.messages == []
    assert plan.latest_round_omitted


# 做什么：确认基本输入超限在接收阶段拒绝，不留下虚假消息或占用
# 入参：隔离工厂和配置替换工具
# 返回：无；静默截断或写入消息时失败
def test_oversized_base_request_is_rejected_before_insert(chat_sessions, monkeypatch):
    chat_session = create_session(chat_sessions, 1)
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", 1)
    with pytest.raises(ModelContextBudgetError):
        prepare_turn(chat_sessions, 1, chat_session.id, str(uuid4()), "保留完整问题")
    with chat_sessions() as db_session:
        assert db_session.scalar(select(func.count()).select_from(ChatMessage)) == 0
        assert db_session.get(ChatSession, chat_session.id).active_request_id is None


# 做什么：确认系统提示词同样受预算限制，模型入口有最终防线
# 入参：配置替换工具
# 返回：无；模型请求漏算系统提示词或未再次检查历史时失败
def test_model_payload_checks_whole_request_and_keeps_original_text(monkeypatch):
    monkeypatch.setattr(model_client, "SYSTEM_PROMPT", "固定系统约定")
    history = [ContextMessage(role="user", content="历史问题"), ContextMessage(role="assistant", content="历史答案")]
    exact_budget = estimate_request_tokens(model_client.SYSTEM_PROMPT, history, "完整问题")
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", exact_budget)
    payload = model_client.model_payload(history, "完整问题")
    assert payload["messages"][0]["content"] == "固定系统约定"
    assert payload["messages"][-1]["content"] == "完整问题"
    assert payload["max_tokens"] == get_settings().llm_max_output_tokens
    monkeypatch.setattr(model_client, "SYSTEM_PROMPT", "固定系统约定" + "新增内容")
    with pytest.raises(ModelContextBudgetError):
        model_client.model_payload(history, "完整问题")


# 做什么：确认最近轮次的候选缓存不会绑定上一次问题的预算结果
# 入参：隔离工厂和替换工具
# 返回：无；命中缓存后长短问题得到相同错误裁剪时失败
def test_cached_candidates_are_rebudgeted_for_each_question(chat_sessions, monkeypatch):
    storage = {}
    monkeypatch.setattr(chat_context, "read_cache", storage.get)
    monkeypatch.setattr(chat_context, "write_cache", partial(remember_cache, storage))
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="答" * 1500))
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "继续")
    input_budget = estimate_request_tokens(model_client.SYSTEM_PROMPT, [], next_turn.content) + 4600
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", input_budget)
    short_plan = chat_context.load_context_plan(chat_sessions, next_turn)
    original_query = chat_context.query_context
    monkeypatch.setattr(chat_context, "query_context", unexpected_query)
    long_turn = next_turn.model_copy(update={"content": "问" * 1000})
    long_plan = chat_context.load_context_plan(chat_sessions, long_turn)
    assert len(short_plan.messages) == 2
    assert long_plan.messages == []
    assert long_plan.latest_round_omitted
    monkeypatch.setattr(chat_context, "query_context", original_query)
    monkeypatch.setattr(chat_context, "read_cache", lambda cache_key: None)
    assert chat_context.load_context_plan(chat_sessions, long_turn) == long_plan


# 做什么：确认超过轮数上限也会告知，而不只提示字符超限
# 入参：隔离工厂和配置替换工具
# 返回：无；轮数截断被当成完整历史时失败
def test_round_limit_reports_older_history(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_context_rounds", 1)
    first_turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, first_turn, TurnResult(status=MessageStatus.COMPLETED, content="第一次回答"))
    second_turn = prepare_turn(chat_sessions, 1, first_turn.session_id, str(uuid4()), "第二个问题")
    finish_turn(chat_sessions, second_turn, TurnResult(status=MessageStatus.COMPLETED, content="第二次回答"))
    third_turn = prepare_turn(chat_sessions, 1, first_turn.session_id, str(uuid4()), "第三个问题")
    plan = chat_context.load_context_plan(chat_sessions, third_turn)
    assert [message.content for message in plan.messages] == ["第二个问题", "第二次回答"]
    assert plan.history_truncated
    assert not plan.latest_round_omitted


# 做什么：确认超长最近轮次会发提示，仍能正常完成且历史不丢失
# 入参：隔离工厂和替换工具
# 返回：无；提示被当失败、旧正文被改动或带上超限历史时失败
def test_context_notice_precedes_normal_stream_and_preserves_history(chat_sessions, monkeypatch):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="长" * 13000))
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "引用上一次")
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    events = event_items(asyncio.run(collect_stream(chat_sessions, next_turn)))
    assert [event[0] for event in events] == ["started", "context", "delta", "delta", "completed"]
    assert events[1][1]["latest_round_omitted"]
    assert events[1][1]["retained_rounds"] == 0
    with chat_sessions() as db_session:
        assert db_session.get(ChatMessage, turn.assistant_id).content == "长" * 13000


# 做什么：确认预算日志只记录统计，不记录问题或历史正文
# 入参：隔离工厂及日志捕获
# 返回：无；日志包含输入正文时失败
def test_budget_logging_is_safe(chat_sessions, caplog):
    turn = new_turn(chat_sessions, "private-budget-question")
    with caplog.at_level(logging.INFO, logger=chat_context.__name__):
        chat_context.load_context_plan(chat_sessions, turn)
    assert "estimated_input_tokens" in caplog.text
    assert "private-budget-question" not in caplog.text


# 做什么：确认新格式缓存中的角色错位或裁剪标记矛盾会回源
# 入参：隔离工厂、替换工具及损坏缓存文本
# 返回：无；损坏内容进入模型候选时失败
@pytest.mark.parametrize("cached", [
    '{"messages":[{"role":"assistant","content":"坏"},{"role":"user","content":"坏"}],"history_truncated":false,"latest_round_omitted":false}',
    '{"messages":[],"history_truncated":false,"latest_round_omitted":true}',
])
def test_invalid_candidate_metadata_or_roles_fall_back(chat_sessions, monkeypatch, cached):
    monkeypatch.setattr(chat_context, "read_cache", lambda cache_key: cached)
    turn = new_turn(chat_sessions)
    candidates = chat_context.load_context_candidates(chat_sessions, turn)
    assert candidates.messages == []
    assert not candidates.history_truncated
