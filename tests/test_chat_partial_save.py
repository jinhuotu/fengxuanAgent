"""验证回答尚未结束时已经持久化，并且保存按时间限频。"""
import asyncio
import subprocess
import sys
from datetime import timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import URL, create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.chat import MessageRole, MessageStatus
from app.llm.client import ModelChunk
from app.models.chat import ChatMessage, ChatSession
from app.schemas.chat import TurnResult
from app.services import chat_context, chat_stream
from app.services.chat_history import current_chat_time
from app.services.chat_turns import finish_turn, interrupt_turn, prepare_turn, save_partial_answer
from tests.chat_support import chat_sessions, history_items, new_turn
from tests.test_chat_stream import collect_stream, completed_model
from tests.partial_save_worker import ABRUPT_EXIT_CODE, FIRST_PART, SECOND_PART


# 做什么：为子进程创建独立且自动清理的 SQLite 路径
# 入参：无，避开 Windows 上可能被其他进程锁定的共享 pytest 临时根目录
# 返回：本用例专用数据库路径，结束时清理临时目录
@pytest.fixture
def partial_database_path():
    with TemporaryDirectory(prefix="fengxuan-chat-partial-") as directory:
        yield Path(directory) / "partial-answer.sqlite"


# 做什么：提供可由用例推进的时钟，不通过真实等待测试保存间隔
# 入参：保存当前秒数的列表
# 返回：当前测试秒数
def read_clock(clock):
    return clock[0]


# 做什么：读取两段模型内容，在流结束之前检查数据库快照
# 入参：会话工厂、请求和可推进的时钟
# 返回：第二段展示时数据库中的正文、状态、占用和历史版本
async def inspect_in_progress_answer(sessions, turn, clock):
    events = chat_stream.stream_turn(sessions, turn)
    try:
        await anext(events)
        await anext(events)
        clock[0] = 2
        await anext(events)
        with sessions() as db_session:
            answer = db_session.get(ChatMessage, turn.assistant_id)
            chat_session = db_session.get(ChatSession, turn.session_id)
            return answer.content, answer.status, chat_session.active_request_id, chat_session.history_version
    finally:
        await events.aclose()


# 做什么：证明流尚未结束时数据库已经保存部分正文，而不是靠 finally 收尾保存
# 入参：隔离会话工厂和替换工具
# 返回：无；只有最终收尾保存的旧实现会得到空正文并失败
def test_partial_answer_is_saved_before_stream_finishes(chat_sessions, monkeypatch):
    clock = [0]
    monkeypatch.setattr(chat_stream, "monotonic", partial(read_clock, clock))
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    turn = new_turn(chat_sessions)
    content, status, active_request_id, version = asyncio.run(inspect_in_progress_answer(chat_sessions, turn, clock))
    assert content == "你好，世界"
    assert status == MessageStatus.GENERATING
    assert active_request_id == turn.request_id
    assert version == turn.history_version


# 做什么：确认未到间隔不写、到期后写、正文不变时不重复写
# 入参：隔离会话工厂和替换工具
# 返回：无；写入次数不符合时间门限时失败
def test_partial_save_is_throttled_and_skips_unchanged_text(chat_sessions, monkeypatch):
    clock = [0]
    writer = MagicMock(return_value=True)
    monkeypatch.setattr(chat_stream, "monotonic", partial(read_clock, clock))
    monkeypatch.setattr(chat_stream, "save_partial_answer", writer)
    turn = new_turn(chat_sessions)
    result = TurnResult(status=MessageStatus.INTERRUPTED, content="第一段")
    reader = chat_stream.PendingStream(task=MagicMock())
    asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    writer.assert_not_called()
    clock[0] = 2
    assert asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    writer.assert_called_once()
    clock[0] = 4
    asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    writer.assert_called_once()
    result.content += "第二段"
    asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    assert writer.call_count == 2


# 做什么：确认写入失败后按同一间隔重试，并且日志不泄露正文或原始错误
# 入参：隔离会话工厂、替换工具和日志捕获
# 返回：无；失败后逐片段重试或泄露正文时失败
def test_partial_save_failure_retries_after_interval(chat_sessions, monkeypatch, caplog):
    clock = [2]
    writer = MagicMock(side_effect=[SQLAlchemyError("private-storage-detail"), True])
    monkeypatch.setattr(chat_stream, "monotonic", partial(read_clock, clock))
    monkeypatch.setattr(chat_stream, "save_partial_answer", writer)
    turn = new_turn(chat_sessions)
    result = TurnResult(status=MessageStatus.INTERRUPTED, content="private-answer-content")
    reader = chat_stream.PendingStream(task=MagicMock())
    assert asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    assert reader.saved_content_length == 0
    clock[0] = 3
    asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    writer.assert_called_once()
    clock[0] = 4
    assert asyncio.run(chat_stream.save_partial_if_due(chat_sessions, turn, result, reader))
    assert writer.call_count == 2
    assert reader.saved_content_length == len(result.content)
    assert "部分回答保存失败" in caplog.text
    assert "private-storage-detail" not in caplog.text
    assert result.content not in caplog.text


# 做什么：提供一个文本片段后持续等待，用来验证等待期间的定期保存
# 入参：历史和问题
# 返回：首个片段后等待，取消时停止
async def waiting_after_one_chunk(history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "等待中的部分回答"}}]})
    await asyncio.sleep(10)


# 做什么：在没有新模型片段时，读取心跳并检查正文是否保存
# 入参：会话工厂、生成请求和可推进时钟
# 返回：心跳到来时数据库中保存的正文
async def inspect_heartbeat_save(sessions, turn, clock):
    events = chat_stream.stream_turn(sessions, turn)
    try:
        await anext(events)
        await anext(events)
        clock[0] = 2
        assert "event: ping" in await anext(events)
        with sessions() as db_session:
            return db_session.get(ChatMessage, turn.assistant_id).content
    finally:
        await events.aclose()


# 做什么：确认等待下一段文字时也会保存，不依赖后续文本到达
# 入参：隔离会话工厂和替换工具
# 返回：无；只有新片段到达才会保存的实现失败
def test_heartbeat_saves_pending_answer_without_new_text(chat_sessions, monkeypatch):
    clock = [0]
    monkeypatch.setattr(chat_stream, "monotonic", partial(read_clock, clock))
    monkeypatch.setattr(chat_stream, "CHAT_STREAM_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(chat_stream, "stream_model", waiting_after_one_chunk)
    turn = new_turn(chat_sessions)
    assert asyncio.run(inspect_heartbeat_save(chat_sessions, turn, clock)) == "等待中的部分回答"


# 做什么：确认快照不会缩短正文或改变生成状态、缓存版本和占用到期时间
# 入参：隔离会话工厂
# 返回：无；快照被当成最终完成或回退正文时失败
def test_partial_snapshot_preserves_generation_metadata(chat_sessions):
    turn = new_turn(chat_sessions)
    with chat_sessions() as db_session:
        expires_at = db_session.get(ChatSession, turn.session_id).generation_expires_at
    assert save_partial_answer(chat_sessions, turn, "较长的部分回答")
    assert save_partial_answer(chat_sessions, turn, "较短")
    with chat_sessions() as db_session:
        answer = db_session.get(ChatMessage, turn.assistant_id)
        chat_session = db_session.get(ChatSession, turn.session_id)
        assert answer.content == "较长的部分回答"
        assert answer.status == MessageStatus.GENERATING
        assert chat_session.active_request_id == turn.request_id
        assert chat_session.history_version == turn.history_version
        assert chat_session.generation_expires_at == expires_at


# 做什么：确认旧请求的定期保存不能覆盖中断记录或新请求
# 入参：隔离会话工厂
# 返回：无；旧请求必须返回 False，新请求保持生成中
def test_stale_partial_save_cannot_modify_new_request(chat_sessions):
    turn = new_turn(chat_sessions)
    save_partial_answer(chat_sessions, turn, "已保存片段")
    interrupt_turn(chat_sessions, 1, turn.session_id, turn.request_id)
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "新问题")
    assert not save_partial_answer(chat_sessions, turn, "迟到的旧请求内容")
    with chat_sessions() as db_session:
        assert db_session.get(ChatMessage, turn.assistant_id).content == "已保存片段"
        assert db_session.get(ChatMessage, next_turn.assistant_id).content == ""
        assert db_session.get(ChatSession, turn.session_id).active_request_id == next_turn.request_id


# 做什么：确认周期保存不能覆盖已经完成的最终结果
# 入参：隔离会话工厂
# 返回：无；最终正文和状态必须保持完成
def test_partial_save_cannot_overwrite_completed_answer(chat_sessions):
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.COMPLETED, content="最终回答"))
    assert not save_partial_answer(chat_sessions, turn, "迟到快照")
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["content"] == "最终回答"
    assert answer["status"] == "completed"


# 做什么：确认有正文的中断快照仍不会成为下一轮模型上下文
# 入参：隔离会话工厂
# 返回：无；未成功完成的轮次必须被排除
def test_partial_snapshot_does_not_enter_successful_context(chat_sessions):
    turn = new_turn(chat_sessions)
    save_partial_answer(chat_sessions, turn, "尚未完成的片段")
    interrupt_turn(chat_sessions, 1, turn.session_id, turn.request_id)
    next_turn = prepare_turn(chat_sessions, 1, turn.session_id, str(uuid4()), "继续提问")
    assert chat_context.load_context(chat_sessions, next_turn) == []


# 做什么：生成三段文字，使最后一段落在保存间隔以内
# 入参：可推进时钟、历史和问题
# 返回：带明确结束原因的模型片段流
async def three_chunk_model(clock, history, question):
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "第一段"}}]})
    clock[0] = 2
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "第二段"}}]})
    clock[0] = 2.1
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": "最后一段"}, "finish_reason": "stop"}]})


# 做什么：确认最终保存不受周期限制，最后一小段不会因未到间隔而丢失
# 入参：隔离会话工厂和替换工具
# 返回：无；完成结果必须包含所有片段
def test_final_save_includes_text_after_latest_partial_snapshot(chat_sessions, monkeypatch):
    clock = [0]
    monkeypatch.setattr(chat_stream, "monotonic", partial(read_clock, clock))
    monkeypatch.setattr(chat_stream, "stream_model", partial(three_chunk_model, clock))
    turn = new_turn(chat_sessions)
    asyncio.run(collect_stream(chat_sessions, turn))
    answer = history_items(chat_sessions, turn.session_id)[1]
    assert answer["content"] == "第一段第二段最后一段"
    assert answer["status"] == "completed"


# 做什么：证明子进程跳过全部收尾退出后，主进程仍可读取并恢复已提交快照
# 入参：独立临时数据库路径
# 返回：无；依赖 finally 才保存、正文丢失或恢复失败都会报错
def test_abrupt_process_exit_preserves_committed_partial_answer(partial_database_path):
    child = subprocess.run(
        [sys.executable, "-m", "tests.partial_save_worker", str(partial_database_path)],
        cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=15,
    )
    assert child.returncode == ABRUPT_EXIT_CODE, child.stderr
    engine = create_engine(URL.create("sqlite", database=str(partial_database_path)))
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sessions.begin() as db_session:
            answer = db_session.scalar(select(ChatMessage).where(ChatMessage.role == MessageRole.ASSISTANT))
            assert answer.content == FIRST_PART + SECOND_PART
            assert answer.status == MessageStatus.GENERATING
            session_id = answer.session_id
            chat_session = db_session.get(ChatSession, session_id)
            assert chat_session.active_request_id == answer.request_id
            chat_session.generation_expires_at = current_chat_time() - timedelta(seconds=1)
        restored = history_items(sessions, session_id)[1]
        assert restored["content"] == FIRST_PART + SECOND_PART
        assert restored["status"] == "interrupted"
    finally:
        engine.dispose()

