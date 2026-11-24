"""对话测试共用的内存数据库和模拟模型，不连接真实服务。"""
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.models.chat import ChatGenerationGate
from app.core.chat import CHAT_GENERATION_GATE_ID
from app.schemas.chat import ChatPageQuery
from app.services.chat_history import create_session, list_messages
from app.services.chat_turns import prepare_turn


# 做什么：为每个用例创建独立内存表和两个账号
# 入参：monkeypatch 用来禁用 Redis 并替换虚拟密钥
# 返回：隔离会话工厂；退出时释放内存数据库
@pytest.fixture
def chat_sessions(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", settings.llm_api_key.__class__("fake-chat-test-key"))
    monkeypatch.setattr(settings, "redis_enabled", False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with sessions.begin() as db_session:
        db_session.add(ChatGenerationGate(id=CHAT_GENERATION_GATE_ID))
        db_session.add(User(id=1, username="owner", password_hash="not-used"))
        db_session.add(User(id=2, username="stranger", password_hash="not-used"))
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

