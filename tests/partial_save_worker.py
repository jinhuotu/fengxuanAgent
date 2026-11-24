"""仅供 pytest 启动的隔离子进程：跳过 finally，验证快照已提交到临时 SQLite。"""
import asyncio
import os
import sys
from unittest.mock import patch
from uuid import uuid4

from pydantic import SecretStr

from app.core import config as configuration_module

ABRUPT_EXIT_CODE = 23
EXPECTED_DELTA_COUNT = 2
SAVE_INTERVAL_SECONDS = 0.1
SECOND_CHUNK_DELAY_SECONDS = 0.15
FIRST_PART = "第一段已生成"
SECOND_PART = "，第二段也已生成"


# 做什么：提供两个有时间间隔的模拟片段，使第二段达到保存门限
# 入参：历史和问题，不访问外部服务
# 返回：两个模型片段，不提供正常结束标记
async def model_for_crash_probe(history, question):
    from app.llm.client import ModelChunk
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": FIRST_PART}}]})
    await asyncio.sleep(SECOND_CHUNK_DELAY_SECONDS)
    yield ModelChunk.model_validate({"choices": [{"delta": {"content": SECOND_PART}}]})


# 做什么：收到第二个增量后立即结束本测试进程，不执行生成器 finally
# 入参：专用 SQLite 会话工厂和测试请求
# 返回：不正常返回；仅退出由 pytest 启动的本子进程
async def exit_after_second_delta(sessions, turn):
    from app.services.chat_stream import stream_turn
    delta_count = 0
    async for event in stream_turn(sessions, turn):
        if event.startswith("event: delta"):
            delta_count += 1
        if delta_count == EXPECTED_DELTA_COUNT:
            os._exit(ABRUPT_EXIT_CODE)


# 做什么：只使用虚拟配置和指定临时数据库运行异常退出检查
# 入参：pytest 临时目录内的 SQLite 路径
# 返回：不会正常返回，任何真实配置文件均不读取
def run_probe(database_path: str) -> None:
    configuration = configuration_module.Settings(
        _env_file=None, db_password="isolated-only", jwt_secret="isolated-only",
        llm_api_key=SecretStr("fake-key"), redis_enabled=False,
        chat_partial_save_interval_seconds=SAVE_INTERVAL_SECONDS,
    )
    with patch.object(configuration_module, "get_settings", return_value=configuration):
        from sqlalchemy import URL, create_engine
        from app.services import chat_stream
        from app.services.chat_history import create_session
        from app.services.chat_turns import prepare_turn
        engine = create_engine(URL.create("sqlite", database=database_path), connect_args={"check_same_thread": False})
        sessions = prepare_probe_sessions(engine)
        chat_session = create_session(sessions, 1)
        turn = prepare_turn(sessions, 1, chat_session.id, str(uuid4()), "异常退出检查")
        chat_stream.stream_model = model_for_crash_probe
        asyncio.run(exit_after_second_delta(sessions, turn))
        raise RuntimeError("子进程未按预期异常退出")


# 做什么：准备专用子进程的隔离表和准入锁记录
# 入参：临时 SQLite 引擎
# 返回：只访问临时库的会话工厂
def prepare_probe_sessions(engine):
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    from app.models.chat import ChatGenerationGate
    from app.core.chat import CHAT_GENERATION_GATE_ID
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db_session:
        db_session.add(ChatGenerationGate(id=CHAT_GENERATION_GATE_ID))
    return sessions


if __name__ == "__main__":
    run_probe(sys.argv[1])

