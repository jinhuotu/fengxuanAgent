"""验证本人隔离、请求幂等和结束状态，全部使用独立内存库。"""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.chat import MessageStatus
from app.core.chat_errors import ChatBusyError, ChatDuplicateError, ChatNotFoundError, ModelNotConfiguredError
from app.core.config import get_settings
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import ChatPageQuery, TurnResult
from app.services.chat_history import create_session, current_chat_time, list_messages, list_sessions
from app.services.chat_turns import finish_turn, interrupt_turn, prepare_turn
from tests.chat_support import chat_sessions, history_items, new_turn


# 做什么：确认列表、历史、发送和中断均不能跨用户访问
# 入参：隔离会话工厂
# 返回：无；任何一次越权成功都使测试失败
def test_session_ownership_applies_to_all_operations(chat_sessions):
    turn = new_turn(chat_sessions)
    assert list_sessions(chat_sessions, 2, ChatPageQuery())["items"] == []
    with pytest.raises(ChatNotFoundError):
        list_messages(chat_sessions, 2, turn.session_id, ChatPageQuery())
    with pytest.raises(ChatNotFoundError):
        prepare_turn(chat_sessions, 2, turn.session_id, str(uuid4()), "不能访问")
    with pytest.raises(ChatNotFoundError):
        interrupt_turn(chat_sessions, 2, turn.session_id, turn.request_id)


# 做什么：确认同会话并发拒绝，重复编号也不会创建多余消息
# 入参：隔离会话工厂
# 返回：无；忙和重复必须给出不同明确错误
def test_busy_and_duplicate_requests_do_not_write_extra_messages(chat_sessions):
    turn = new_turn(chat_sessions)
    with pytest.raises(ChatBusyError):
        prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "第二个问题")
    with pytest.raises(ChatDuplicateError):
        prepare_turn(chat_sessions, 1, turn.session_id, turn.request_id, turn.content)
    assert len(history_items(chat_sessions, turn.session_id)) == 2


# 做什么：确认已中断的旧请求不能完成或清除新请求的占用
# 入参：隔离会话工厂
# 返回：无；旧部分文字可保留，新请求仍为生成中
def test_interrupted_old_result_cannot_overwrite_new_turn(chat_sessions):
    turn = new_turn(chat_sessions)
    assert interrupt_turn(chat_sessions, 1, turn.session_id, turn.request_id)
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "新问题")
    assert not finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="迟到的部分回答"))
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, turn.session_id).active_request_id == next_turn.request_id
        assistant = db_session.get(ChatMessage, turn.assistant_id)
        assert assistant.status == MessageStatus.INTERRUPTED
        assert assistant.content == "迟到的部分回答"


# 做什么：确认过期占用在历史查询时恢复，并允许继续提问
# 入参：隔离会话工厂
# 返回：无；遗留生成必须变为中断
def test_expired_generation_is_recovered(chat_sessions):
    turn = new_turn(chat_sessions)
    with chat_sessions.begin() as db_session:
        chat_session = db_session.get(ChatSession, turn.session_id)
        chat_session.generation_expires_at = current_chat_time() - timedelta(seconds=1)
    assert history_items(chat_sessions, turn.session_id)[1]["status"] == "interrupted"
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "恢复后发送")
    assert next_turn.assistant_id != turn.assistant_id


# 做什么：确认未配置密钥不写问题或生成占用
# 入参：隔离会话工厂和配置替换工具
# 返回：无；失败后数据库保持空会话
def test_missing_key_does_not_create_messages(chat_sessions, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", settings.llm_api_key.__class__(""))
    chat_session = create_session(chat_sessions, 1)
    with pytest.raises(ModelNotConfiguredError):
        prepare_turn(chat_sessions, 1, chat_session.id, str(uuid4()), "问题")
    assert history_items(chat_sessions, chat_session.id) == []


# 做什么：确认分页稳定、消息公开结构不泄露内部字段
# 入参：隔离会话工厂
# 返回：无；新旧页消息不得重叠
def test_message_history_pagination_is_ordered(chat_sessions):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="回答"))
    latest = list_messages(chat_sessions, 1, turn.session_id, ChatPageQuery(page_size=1))
    earlier = list_messages(chat_sessions, 1, turn.session_id, ChatPageQuery(page=2, page_size=1))
    assert latest["items"][0]["role"] == "assistant"
    assert earlier["items"][0]["role"] == "user"
    assert latest["total"] == 2
    assert "user_id" not in latest["items"][0]

