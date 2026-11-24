"""对话测试共用的内存库、文件库和模拟模型，不连接真实服务。"""
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.models.chat import ChatGenerationGate
from app.core.chat import CHAT_GENERATION_GATE_ID
from app.schemas.chat import ChatPageQuery
from app.services.chat_history import create_session, list_messages
from app.services.chat_turns import prepare_turn

# 新手注意：只有一个线程用库的用例走内存库；真的起服务、多线程并发读写的用例
# 必须走文件库，因为内存库靠 StaticPool 全进程只有一条连接，两个线程同时用会读出
# 别人的半截行（created_at 变成 None、内容变空串），这不是偶发而是必然的竞态。


# 做什么：把测试配置换成虚拟密钥并关掉 Redis
# 入参：monkeypatch 替换工具
# 返回：无；只影响当前用例
def isolate_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", settings.llm_api_key.__class__("fake-chat-test-key"))
    monkeypatch.setattr(settings, "redis_enabled", False)


# 做什么：建表并灌入准入锁和两个账号
# 入参：已经建好的数据库引擎
# 返回：绑定该引擎的会话工厂；种子数据只写一次
def prepare_sessions(engine):
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with sessions.begin() as db_session:
        db_session.add(ChatGenerationGate(id=CHAT_GENERATION_GATE_ID))
        db_session.add(User(id=1, username="owner", password_hash="not-used"))
        db_session.add(User(id=2, username="stranger", password_hash="not-used"))
    return sessions


# 做什么：把文件库切成 WAL 日志模式，让读和写可以并行
# 入参：sqlite 原始连接和连接记录
# 返回：无；每建立一条连接时执行一次
def enable_wal(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# 做什么：为每个用例创建独立内存表和两个账号
# 入参：monkeypatch 用来禁用 Redis 并替换虚拟密钥
# 返回：隔离会话工厂；退出时释放内存数据库
@pytest.fixture
def chat_sessions(monkeypatch):
    isolate_settings(monkeypatch)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = prepare_sessions(engine)
    yield sessions
    engine.dispose()


# 做什么：为多线程用例创建临时文件库，每个线程各拿一条独立连接
# 入参：pytest 临时目录和 monkeypatch 替换工具
# 返回：隔离会话工厂；退出时释放文件库
@pytest.fixture
def file_chat_sessions(tmp_path, monkeypatch):
    isolate_settings(monkeypatch)
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chat-verify.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    event.listen(engine, "connect", enable_wal)
    sessions = prepare_sessions(engine)
    yield sessions
    engine.dispose()


# 做什么：创建一轮属于用户一的测试请求
# 入参：隔离会话工厂和问题正文
# 返回：生成上下文，不调用真实模型
def new_turn(sessions, content="测试问题"):
    chat_session = create_session(sessions, 1)
    return prepare_turn(sessions, 1, chat_session.id, str(uuid4()), content)


# 做什么：便捷读取测试会话第一页消息
# 入参：会话工厂和会话编号
# 返回：消息数组
def history_items(sessions, session_id):
    return list_messages(sessions, 1, session_id, ChatPageQuery())["items"]


# 做什么：解析完整 SSE 文本为事件列表
# 入参：异步生成器收集后的文本
# 返回：事件名称与 JSON 字典，不解析半帧
def event_items(content):
    events = []
    for frame in content.strip().split("\n\n"):
        event_name, payload = frame.split("\n", 1)
        events.append((event_name.removeprefix("event: "), json.loads(payload.removeprefix("data: "))))
    return events
