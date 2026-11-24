"""验证无需用户访问的过期恢复，全部使用隔离内存库。"""
import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app import main
from app.core.chat import MessageStatus
from app.core.config import get_settings
from app.db import session as database_session
from app.models.chat import ChatMessage, ChatSession
from app.services.chat_history import current_chat_time
from app.services import chat_recovery
from app.services.chat_turns import finish_turn, prepare_turn, save_partial_answer
from app.schemas.chat import TurnResult
from tests.chat_support import chat_sessions, new_turn


# 做什么：模拟退出后留下的生成占用和已提交正文
# 入参：隔离会话工厂和轮次
# 返回：无；只修改测试数据库
def expire_turn(sessions, turn):
    with sessions.begin() as db_session:
        chat_session = db_session.get(ChatSession, turn.session_id)
        chat_session.generation_expires_at = current_chat_time() - timedelta(seconds=1)
        db_session.get(ChatMessage, turn.assistant_id).content = "已提交的部分回答"


# 做什么：轮询原始数据库，避免历史接口的惰性恢复掩盖缺陷
# 入参：隔离会话工厂和轮次
# 返回：无；后台任务未恢复时断言失败
async def wait_for_recovery(sessions, turn):
    for attempt in range(100):
        with sessions() as db_session:
            if db_session.get(ChatSession, turn.session_id).active_request_id is None:
                return
        await asyncio.sleep(0.01)
    raise AssertionError("无需访问历史也应自动恢复过期占用")


# 做什么：确认应用启动即自动恢复，并保留正文和有效请求
# 入参：隔离会话工厂和替换工具
# 返回：无；启动、恢复或有效请求保护不正确时失败
def test_lifespan_recovers_without_history_access(chat_sessions, monkeypatch):
    expired_turn = new_turn(chat_sessions)
    active_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, expired_turn)
    monkeypatch.setattr(database_session, "SessionLocal", chat_sessions)
    monkeypatch.setattr(get_settings(), "chat_recovery_enabled", True, raising=False)
    asyncio.run(check_lifespan_recovery(chat_sessions, expired_turn, active_turn))


# 做什么：进入真实应用生命周期并验证数据库状态
# 入参：隔离工厂、过期轮次和有效轮次
# 返回：无；退出上下文会等待后台任务关闭
async def check_lifespan_recovery(sessions, expired_turn, active_turn):
    async with main.lifespan(main.app):
        await wait_for_recovery(sessions, expired_turn)
        with sessions() as db_session:
            assistant = db_session.get(ChatMessage, expired_turn.assistant_id)
            assert assistant.status == MessageStatus.INTERRUPTED
            assert assistant.content == "已提交的部分回答"
            assert assistant.error_code == "generation_expired"
            assert db_session.get(ChatSession, expired_turn.session_id).history_version == 1
            assert db_session.get(ChatSession, active_turn.session_id).active_request_id == active_turn.request_id


# 做什么：确认批量上限和重复扫描不会重复增加历史版本
# 入参：隔离会话工厂
# 返回：无；超过上限或重复恢复时失败
def test_batch_is_bounded_and_idempotent(chat_sessions):
    first_turn = new_turn(chat_sessions)
    second_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, first_turn)
    expire_turn(chat_sessions, second_turn)
    assert chat_recovery.recover_expired_batch(chat_sessions, 1) == 1
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, second_turn.session_id).active_request_id == second_turn.request_id
    assert chat_recovery.recover_expired_batch(chat_sessions, 1) == 1
    assert chat_recovery.recover_expired_batch(chat_sessions, 1) == 0
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, first_turn.session_id).history_version == 1
        assert db_session.get(ChatSession, second_turn.session_id).history_version == 1


# 做什么：确认软删除会话不处理，缺失到期时间沿用惰性恢复规则
# 入参：隔离会话工厂
# 返回：无；删除状态或损坏占用处理错误时失败
def test_deleted_sessions_are_skipped_and_missing_expiry_is_recovered(chat_sessions):
    deleted_turn = new_turn(chat_sessions)
    missing_expiry_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, deleted_turn)
    with chat_sessions.begin() as db_session:
        db_session.get(ChatSession, deleted_turn.session_id).is_deleted = 1
        db_session.get(ChatSession, missing_expiry_turn.session_id).generation_expires_at = None
    assert chat_recovery.recover_expired_batch(chat_sessions, 100) == 1
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, deleted_turn.session_id).active_request_id == deleted_turn.request_id
        assert db_session.get(ChatSession, missing_expiry_turn.session_id).active_request_id is None


# 做什么：确认恢复后旧快照和迟到收尾不能解除新请求占用
# 入参：隔离会话工厂
# 返回：无；新请求、历史版本或消息状态被旧请求覆盖时失败
def test_recovered_request_cannot_replace_new_generation(chat_sessions):
    expired_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, expired_turn)
    assert chat_recovery.recover_expired_batch(chat_sessions, 100) == 1
    next_turn = prepare_turn(chat_sessions, 1, expired_turn.session_id, str(uuid4()), "新问题")
    assert not save_partial_answer(chat_sessions, expired_turn, "旧快照")
    assert not finish_turn(chat_sessions, expired_turn, TurnResult(status=MessageStatus.COMPLETED, content="旧结果"))
    assert chat_recovery.recover_expired_batch(chat_sessions, 100) == 0
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, next_turn.session_id).active_request_id == next_turn.request_id
        assert db_session.get(ChatMessage, next_turn.assistant_id).status == MessageStatus.GENERATING
        assert db_session.get(ChatMessage, expired_turn.assistant_id).status == MessageStatus.INTERRUPTED


# 做什么：确认数据库故障回滚整批，不留下半恢复记录
# 入参：隔离会话工厂和替换工具
# 返回：无；事务未回滚时失败
def test_batch_database_failure_rolls_back(chat_sessions, monkeypatch):
    expired_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, expired_turn)
    original_recover = chat_recovery.recover_expired

    # 做什么：模拟更新后数据库报错
    # 入参：当前事务和已锁定会话
    # 返回：始终抛数据库异常
    def fail_after_update(db_session, chat_session):
        original_recover(db_session, chat_session)
        raise SQLAlchemyError("fake-database-secret")

    monkeypatch.setattr(chat_recovery, "recover_expired", fail_after_update)
    with pytest.raises(SQLAlchemyError):
        chat_recovery.recover_expired_batch(chat_sessions, 100)
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, expired_turn.session_id).active_request_id == expired_turn.request_id
        assert db_session.get(ChatMessage, expired_turn.assistant_id).status == MessageStatus.GENERATING


# 做什么：确认数据库暂时不可用会重试且日志不含原始错误正文
# 入参：隔离工厂、替换工具和日志捕获
# 返回：无；未重试或泄露错误正文时失败
def test_background_failure_retries_safely(chat_sessions, monkeypatch, caplog):
    expired_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, expired_turn)
    original_batch = chat_recovery.recover_expired_batch
    attempts = []

    # 做什么：仅第一次扫描模拟数据库故障
    # 入参：隔离工厂和批量上限
    # 返回：第一次抛异常，其余正常恢复
    def fail_first_batch(sessions, batch_size):
        attempts.append(batch_size)
        if len(attempts) == 1:
            raise SQLAlchemyError("fake-database-secret")
        return original_batch(sessions, batch_size)

    monkeypatch.setattr(chat_recovery, "recover_expired_batch", fail_first_batch)
    monkeypatch.setattr(get_settings(), "chat_recovery_enabled", True)
    monkeypatch.setattr(get_settings(), "chat_recovery_interval_seconds", 0.1)
    with caplog.at_level(logging.ERROR, logger=chat_recovery.__name__):
        asyncio.run(run_recovery_until_saved(chat_sessions, expired_turn))
    assert len(attempts) >= 2
    assert "下一轮重试" in caplog.text
    assert "fake-database-secret" not in caplog.text


# 做什么：等待恢复并检查生命周期退出不会留下任务
# 入参：隔离会话工厂和过期轮次
# 返回：无；后台未关闭或恢复失败时断言失败
async def run_recovery_until_saved(sessions, turn):
    async with chat_recovery.recovery_lifespan(sessions):
        await wait_for_recovery(sessions, turn)
    for pending_task in asyncio.all_tasks():
        assert pending_task.get_name() != chat_recovery.RECOVERY_TASK_NAME


# 做什么：确认启动后才过期的记录也由定时扫描处理
# 入参：隔离工厂和替换工具
# 返回：无；只执行启动扫描时失败
def test_periodic_scan_recovers_later_expiry(chat_sessions, monkeypatch):
    active_turn = new_turn(chat_sessions)
    monkeypatch.setattr(get_settings(), "chat_recovery_enabled", True)
    monkeypatch.setattr(get_settings(), "chat_recovery_interval_seconds", 0.1)
    asyncio.run(expire_after_start(chat_sessions, active_turn))


# 做什么：在首次扫描结束之后制造过期状态
# 入参：隔离工厂和初始有效轮次
# 返回：无；后续扫描必须自动恢复
async def expire_after_start(sessions, turn):
    async with chat_recovery.recovery_lifespan(sessions):
        await asyncio.sleep(0.05)
        expire_turn(sessions, turn)
        await wait_for_recovery(sessions, turn)


# 做什么：确认关闭开关后不创建任务或扫描数据库
# 入参：隔离工厂和替换工具
# 返回：无；后台意外访问数据库时失败
def test_disabled_recovery_does_not_scan(chat_sessions, monkeypatch):
    expired_turn = new_turn(chat_sessions)
    expire_turn(chat_sessions, expired_turn)
    monkeypatch.setattr(get_settings(), "chat_recovery_enabled", False)
    asyncio.run(check_disabled_recovery(chat_sessions))
    with chat_sessions() as db_session:
        assert db_session.get(ChatSession, expired_turn.session_id).active_request_id == expired_turn.request_id


# 做什么：进入禁用的恢复上下文并检查无后台任务
# 入参：隔离会话工厂
# 返回：无；创建了任务则失败
async def check_disabled_recovery(sessions):
    async with chat_recovery.recovery_lifespan(sessions):
        for pending_task in asyncio.all_tasks():
            assert pending_task.get_name() != chat_recovery.RECOVERY_TASK_NAME
