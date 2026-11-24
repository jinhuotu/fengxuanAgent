"""全站并发额度回归，使用隔离内存数据库。"""
from uuid import uuid4
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import BusinessError
from app.core.chat import CHAT_GENERATION_GATE_ID, MessageStatus
from app.core.chat_errors import ChatBusyError, ChatDuplicateError, ChatCapacityUnavailableError
from app.core.config import get_settings
from app.models.chat import ChatGenerationGate, ChatMessage, ChatSession
from app.schemas.chat import TurnResult
from app.services.chat_history import create_session, current_chat_time
from app.services.chat_turns import finish_turn, interrupt_turn, prepare_turn
from tests.chat_support import chat_sessions, new_turn


# 做什么：确认第十一个生成在写入任何消息前被拒绝
# 入参：隔离会话工厂
# 返回：无；全站上限失效或拒绝后残留消息时失败
def test_eleventh_generation_is_rejected_without_writing(chat_sessions):
    for question_number in range(10):
        new_turn(chat_sessions, f"问题 {question_number}")
    chat_session = create_session(chat_sessions, 1)
    with pytest.raises(BusinessError) as captured:
        prepare_turn(chat_sessions, 1, chat_session.id, str(uuid4()), "超出额度")
    assert captured.value.http_status == 429
    assert captured.value.code == 3004
    with chat_sessions() as db_session:
        message_count = select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == chat_session.id)
        assert db_session.scalar(message_count) == 0
        assert db_session.get(ChatSession, chat_session.id).active_request_id is None


# 做什么：确认同一用户可以跨会话并行且上限由配置决定
# 入参：隔离工厂和替换工具
# 返回：无；错误增加用户上限或忽略全站配置时失败
def test_same_user_can_fill_configured_global_capacity(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 2)
    first_turn = new_turn(chat_sessions)
    second_turn = new_turn(chat_sessions)
    assert first_turn.session_id != second_turn.session_id
    with pytest.raises(BusinessError) as captured:
        new_turn(chat_sessions)
    assert captured.value.code == 3004


# 做什么：确认额度按全站统计，不能靠换用户绕过
# 入参：隔离工厂和替换工具
# 返回：无；其他用户仍能越过上限时失败
def test_global_limit_is_shared_by_users(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    new_turn(chat_sessions)
    other_session = create_session(chat_sessions, 2)
    with pytest.raises(BusinessError) as captured:
        prepare_turn(chat_sessions, 2, other_session.id, str(uuid4()), "另一用户")
    assert captured.value.code == 3004


# 做什么：确认成功和失败收尾都会释放并发名额
# 入参：隔离工厂、替换工具和结束状态
# 返回：无；收尾后不能创建新请求时失败
@pytest.mark.parametrize("status", [MessageStatus.COMPLETED, MessageStatus.FAILED])
def test_final_result_releases_capacity(chat_sessions, monkeypatch, status):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    turn = new_turn(chat_sessions)
    assert finish_turn(chat_sessions, turn, TurnResult(status=status, content="已保存"))
    assert new_turn(chat_sessions).request_id != turn.request_id


# 做什么：确认显式停止释放名额，旧请求晚到不影响新名额
# 入参：隔离工厂和替换工具
# 返回：无；名额泄漏或旧请求解除新占用时失败
def test_interrupt_releases_capacity_without_stale_release(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    old_turn = new_turn(chat_sessions)
    assert interrupt_turn(chat_sessions, 1, old_turn.session_id, old_turn.request_id)
    next_turn = new_turn(chat_sessions)
    assert not finish_turn(chat_sessions, old_turn, TurnResult(status=MessageStatus.COMPLETED, content="迟到"))
    with pytest.raises(BusinessError) as captured:
        new_turn(chat_sessions)
    assert captured.value.code == 3004
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, next_turn.session_id).active_request_id == next_turn.request_id


# 做什么：确认过期、缺失到期时间及软删除会话不占有效额度
# 入参：隔离工厂、替换工具和失效方式
# 返回：无；失效占用堵住新请求时失败
@pytest.mark.parametrize("invalid_state", ["expired", "missing_expiry", "deleted"])
def test_invalid_leases_do_not_consume_capacity(chat_sessions, monkeypatch, invalid_state):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    old_turn = new_turn(chat_sessions)
    with chat_sessions.begin() as db_session:
        chat_session = db_session.get(ChatSession, old_turn.session_id)
        if invalid_state == "expired":
            chat_session.generation_expires_at = current_chat_time() - timedelta(seconds=1)
        if invalid_state == "missing_expiry":
            chat_session.generation_expires_at = None
        if invalid_state == "deleted":
            chat_session.is_deleted = 1
    assert new_turn(chat_sessions).session_id != old_turn.session_id


# 做什么：确认全站满额时仍保留同会话忙与重复请求的原有语义
# 入参：隔离工厂和替换工具
# 返回：无；原有优先校验失效时失败
def test_duplicate_and_same_session_busy_remain_distinct(chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    turn = new_turn(chat_sessions)
    with pytest.raises(ChatDuplicateError):
        prepare_turn(chat_sessions, 1, turn.session_id, turn.request_id, turn.content)
    with pytest.raises(ChatBusyError):
        prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "同会话第二个")


# 做什么：确认准入记录不可用时拒绝请求，不能悄悄取消全站保护
# 入参：隔离会话工厂
# 返回：无；记录失效后仍写入消息时失败
def test_unavailable_gate_fails_closed(chat_sessions):
    with chat_sessions.begin() as db_session:
        db_session.get(ChatGenerationGate, CHAT_GENERATION_GATE_ID).is_deleted = 1
    with pytest.raises(ChatCapacityUnavailableError):
        new_turn(chat_sessions)
    with chat_sessions() as db_session:
        assert db_session.scalar(select(func.count()).select_from(ChatMessage)) == 0
