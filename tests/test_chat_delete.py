"""删除与清空会话：归属、生成中拒绝、软删与容量释放。"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.chat import MessageStatus
from app.core.chat_errors import ChatBusyError, ChatNotFoundError
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import ChatPageQuery, TurnResult
from app.services.chat_history import (
    clear_sessions, create_session, current_chat_time, delete_session, list_messages, list_sessions,
)
from app.services.chat_turns import finish_turn, prepare_turn
from tests.chat_support import chat_sessions, new_turn


# 做什么：造一条完整的“一问一答”会话
# 入参：会话工厂和问题正文
# 返回：已结束、无占用的会话编号及消息条数
def completed_session(sessions, content):
    chat_session = create_session(sessions, 1)
    turn = prepare_turn(sessions, 1, chat_session.id, str(uuid4()), content)
    finish_turn(sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="回答"))
    return chat_session.id


# 做什么：确认删除后列表和历史都不可见，消息仍在库里但已标记删除
# 入参：隔离会话工厂
# 返回：任一可见性判断错误都使测试失败
def test_delete_session_soft_deletes_messages_and_hides_session(chat_sessions):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="已保存回答"))

    result = delete_session(chat_sessions, 1, turn.session_id)

    assert result.session_id == turn.session_id
    assert result.deleted_messages == 2
    assert list_sessions(chat_sessions, 1, ChatPageQuery())["items"] == []
    with pytest.raises(ChatNotFoundError):
        list_messages(chat_sessions, 1, turn.session_id, ChatPageQuery())
    with chat_sessions() as db_session:
        session_row = db_session.get(ChatSession, turn.session_id)
        assert session_row.is_deleted == 1
        assert session_row.active_request_id is None
        messages = list(db_session.scalars(select(ChatMessage).where(ChatMessage.session_id == turn.session_id)))
        assert len(messages) == 2
        assert all(message.is_deleted == 1 for message in messages)


# 做什么：确认不能删除或清空别人的会话
# 入参：隔离会话工厂
# 返回：越权成功时测试失败
def test_delete_and_clear_reject_other_user(chat_sessions):
    turn = new_turn(chat_sessions)
    with pytest.raises(ChatNotFoundError):
        delete_session(chat_sessions, 2, turn.session_id)
    assert clear_sessions(chat_sessions, 2).deleted_sessions == 0
    assert list_sessions(chat_sessions, 1, ChatPageQuery())["total"] == 1


# 做什么：确认正在生成的会话不允许删除
# 入参：隔离会话工厂
# 返回：删除成功时测试失败
def test_delete_rejects_session_that_is_generating(chat_sessions):
    turn = new_turn(chat_sessions)
    with pytest.raises(ChatBusyError):
        delete_session(chat_sessions, 1, turn.session_id)
    assert list_sessions(chat_sessions, 1, ChatPageQuery())["total"] == 1


# 做什么：确认占用已过期时先恢复成中断再删除，不误报忙
# 入参：隔离会话工厂
# 返回：过期判断错误时测试失败
def test_delete_recovers_expired_generation_first(chat_sessions):
    turn = new_turn(chat_sessions)
    with chat_sessions.begin() as db_session:
        chat_session = db_session.get(ChatSession, turn.session_id)
        chat_session.generation_expires_at = current_chat_time().replace(year=2020)
    result = delete_session(chat_sessions, 1, turn.session_id)
    assert result.deleted_messages == 2


# 做什么：确认清空按条数统计，且两条会话的消息一起软删
# 入参：隔离会话工厂
# 返回：统计或残留数据不对时测试失败
def test_clear_sessions_removes_all_owned_sessions(chat_sessions):
    first_id = completed_session(chat_sessions, "第一问")
    second_id = completed_session(chat_sessions, "第二问")

    result = clear_sessions(chat_sessions, 1)

    assert result.deleted_sessions == 2
    assert result.deleted_messages == 4
    assert list_sessions(chat_sessions, 1, ChatPageQuery())["total"] == 0
    with chat_sessions() as db_session:
        assert all(row.is_deleted == 1 for row in db_session.scalars(select(ChatSession)))
        messages = list(db_session.scalars(select(ChatMessage)))
        assert all(message.is_deleted == 1 for message in messages)
        assert len(messages) == 4
    assert first_id != second_id


# 做什么：确认任一会话在生成时整批清空被拒绝，且什么都没删
# 入参：隔离会话工厂
# 返回：部分删除或返回成功时测试失败
def test_clear_sessions_rejects_when_any_session_is_generating(chat_sessions):
    completed_session(chat_sessions, "已完成的一问")
    new_turn(chat_sessions)
    with pytest.raises(ChatBusyError):
        clear_sessions(chat_sessions, 1)
    assert list_sessions(chat_sessions, 1, ChatPageQuery())["total"] == 2


# 做什么：确认删除过期占用的会话后，全站占用统计不再包含它
# 入参：隔离会话工厂
# 返回：占用仍被计入时测试失败
def test_delete_frees_generation_capacity(chat_sessions):
    turn = new_turn(chat_sessions)
    # 先让占用过期：删除会把它恢复成中断，再软删
    with chat_sessions.begin() as db_session:
        db_session.get(ChatSession, turn.session_id).generation_expires_at = current_chat_time().replace(year=2020)
    delete_session(chat_sessions, 1, turn.session_id)
    with chat_sessions() as db_session:
        occupied = db_session.scalar(
            select(ChatSession).where(ChatSession.is_deleted == 0, ChatSession.active_request_id.is_not(None))
        )
        assert occupied is None
