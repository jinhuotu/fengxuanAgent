"""请求定位与最近失败问题的原子重试校验，全部使用隔离内存库。"""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.chat import MessageStatus
from app.core.chat_errors import ChatNotFoundError, ChatRequestNotFoundError, ChatRetryNotAllowedError
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import TurnResult
from app.services.chat_history import current_chat_time
from app.services.chat_requests import get_request_result
from app.services.chat_turns import finish_turn, interrupt_turn, prepare_turn
from tests.chat_support import chat_sessions, new_turn


# 做什么：确认所有终态都能直接查询，且只允许最近失败或中断轮次重试
# 入参：隔离工厂、结束状态和预期资格
# 返回：无；状态、部分正文或资格错误时失败
@pytest.mark.parametrize("status,can_retry", [
    (MessageStatus.COMPLETED, False), (MessageStatus.FAILED, True), (MessageStatus.INTERRUPTED, True),
])
def test_request_result_preserves_terminal_status(chat_sessions, status, can_retry):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=status, content="已保存正文", error_code="safe_failure"))
    result = get_request_result(chat_sessions, 1, turn.session_id, turn.request_id)
    assert result.question.content == turn.content
    assert result.answer.content == "已保存正文"
    assert result.answer.error_code == "safe_failure"
    assert result.can_retry is can_retry
    assert result.session_busy is False


# 做什么：确认查询权限和软删除隔离，未知请求不创建任何消息
# 入参：隔离工厂
# 返回：无；越权成功或软删除数据泄露时失败
def test_request_lookup_checks_ownership_and_deleted_messages(chat_sessions):
    turn = new_turn(chat_sessions)
    with pytest.raises(ChatNotFoundError):
        get_request_result(chat_sessions, 2, turn.session_id, turn.request_id)
    with pytest.raises(ChatRequestNotFoundError):
        get_request_result(chat_sessions, 1, turn.session_id, str(uuid4()))
    with chat_sessions.begin() as db_session:
        db_session.get(ChatMessage, turn.assistant_id).is_deleted = 1
    with pytest.raises(ChatRequestNotFoundError):
        get_request_result(chat_sessions, 1, turn.session_id, turn.request_id)


# 做什么：确认结果查询会恢复过期占用，不需要先分页读取历史
# 入参：隔离工厂
# 返回：无；过期后仍生成或正文丢失时失败
def test_lookup_recovers_expired_generation(chat_sessions):
    turn = new_turn(chat_sessions)
    with chat_sessions.begin() as db_session:
        db_session.get(ChatSession, turn.session_id).generation_expires_at = current_chat_time() - timedelta(seconds=1)
        db_session.get(ChatMessage, turn.assistant_id).content = "部分回答"
    result = get_request_result(chat_sessions, 1, turn.session_id, turn.request_id)
    assert result.answer.status == "interrupted"
    assert result.answer.error_code == "generation_expired"
    assert result.answer.content == "部分回答"
    assert result.can_retry


# 做什么：确认旧轮次查询不受历史页大小限制且不能重试旧问题
# 入参：隔离工厂
# 返回：无；旧结果丢失或资格校验错误时失败
def test_old_request_remains_queryable_but_cannot_retry(chat_sessions):
    first_turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, first_turn, TurnResult(status=MessageStatus.FAILED, content="旧部分回答"))
    for question_number in range(12):
        turn = prepare_turn(chat_sessions, 1, first_turn.session_id, str(uuid4()), f"后续问题 {question_number}")
        finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="后续回答"))
    result = get_request_result(chat_sessions, 1, first_turn.session_id, first_turn.request_id)
    assert result.answer.content == "旧部分回答"
    assert not result.can_retry
    with pytest.raises(ChatRetryNotAllowedError):
        prepare_turn(chat_sessions, 1, first_turn.session_id, str(uuid4()), first_turn.content, first_turn.request_id)


# 做什么：确认手动重试创建新编号和新轮次，原失败记录仍保留
# 入参：隔离工厂及可重试终态
# 返回：无；覆盖旧记录或重用原编号时失败
@pytest.mark.parametrize("status", [MessageStatus.FAILED, MessageStatus.INTERRUPTED])
def test_retry_creates_new_turn_without_overwriting_history(chat_sessions, status):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=status, content="保留旧正文"))
    retry = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), turn.content, turn.request_id)
    assert retry.request_id != turn.request_id
    assert retry.assistant_id != turn.assistant_id
    result = get_request_result(chat_sessions, 1, turn.session_id, turn.request_id)
    assert result.answer.content == "保留旧正文"
    assert result.session_busy
    assert not result.can_retry


# 做什么：确认不能重试成功回答或篡改原问题
# 入参：隔离工厂及场景
# 返回：无；违规重试被接收时失败
@pytest.mark.parametrize("completed,content", [(True, "测试问题"), (False, "篡改的问题")])
def test_retry_rejects_completed_answer_or_changed_question(chat_sessions, completed, content):
    turn = new_turn(chat_sessions)
    status = MessageStatus.FAILED
    if completed:
        status = MessageStatus.COMPLETED
    finish_turn(chat_sessions, turn, TurnResult(status=status, content="已保存正文"))
    with pytest.raises(ChatRetryNotAllowedError):
        prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), content, turn.request_id)


# 做什么：确认查询资格之后出现新轮次，提交时仍会再次拒绝旧候选
# 入参：隔离工厂
# 返回：无；仅依赖前端旧资格时失败
def test_retry_eligibility_is_rechecked_inside_submission(chat_sessions):
    turn = new_turn(chat_sessions)
    interrupt_turn(chat_sessions, 1, turn.session_id, turn.request_id)
    assert get_request_result(chat_sessions, 1, turn.session_id, turn.request_id).can_retry
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "新的问题")
    finish_turn(chat_sessions, next_turn, TurnResult(status=MessageStatus.COMPLETED, content="新回答"))
    with pytest.raises(ChatRetryNotAllowedError):
        prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), turn.content, turn.request_id)


# 做什么：旧轮次完成信息必须同时携带最新活动请求，避免观察页误判会话空闲
# 入参：独立数据库
# 返回：无；新旧 UUID 或历史版本混淆时失败
def test_old_result_includes_newer_active_request(chat_sessions):
    first_turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, first_turn, TurnResult(status=MessageStatus.COMPLETED, content="第一轮"))
    next_turn = prepare_turn(chat_sessions, 1, first_turn.session_id, str(uuid4()), "第二轮")
    result = get_request_result(chat_sessions, 1, first_turn.session_id, first_turn.request_id)
    assert result.request_id == first_turn.request_id
    assert result.answer.status == "completed"
    assert result.active_request_id == next_turn.request_id
    assert result.history_version == 1
    assert result.session_busy
