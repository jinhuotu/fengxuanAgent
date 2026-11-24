"""显式运行的 MySQL 实测：只允许新建的 chat_verify 隔离库，不删除任何表。"""
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text, func, select
from sqlalchemy.orm import sessionmaker

from app.core.chat_errors import ChatBusyError, ChatCapacityError
from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.models.chat import ChatSession
from app.services.chat_history import create_session
from app.services.chat_turns import prepare_turn


# 做什么：在指定隔离库执行新增迁移，不使用 create_all 代替对话表迁移
# 入参：pytest 用例提供的专用测试配置
# 返回：MySQL 会话工厂；已有对话表时直接失败，不清理已有数据
@pytest.fixture(scope="module")
def mysql_sessions():
    settings = get_settings()
    assert settings.db_name == "chat_verify", "仅允许明确指定的独立测试库"
    assert settings.db_password == "chat-isolated-only", "禁止使用真实库凭证"
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    migration = load_chat_migration()
    capacity_migration = load_chat_migration("7e9204b6d815_add_chat_generation_gate.py")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            capacity_migration.upgrade()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db_session:
        db_session.add(User(id=1, username="mysql_owner", password_hash="not-used"))
    yield sessions
    engine.dispose()


# 做什么：按路径读取项目迁移，避免与已安装的 alembic 包混淆
# 入参：迁移文件名，默认首次对话迁移
# 返回：迁移模块，不连接数据库
def load_chat_migration(filename="24c713a60f20_create_chat_tables.py"):
    migration_path = Path(__file__).parents[1] / "alembic/versions" / filename
    specification = spec_from_file_location("chat_migration", migration_path)
    migration = module_from_spec(specification)
    specification.loader.exec_module(migration)
    return migration


# 做什么：核对迁移与模型一致，逐列验证中文注释已经进入 MySQL
# 入参：独立 MySQL 会话工厂
# 返回：无；结构差异、缺失列注释或表注释均失败
def test_mysql_migration_matches_models_and_comments(mysql_sessions):
    with mysql_sessions() as db_session:
        connection = db_session.connection()
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        statements = [
            ("SHOW FULL COLUMNS FROM chat_sessions", "SHOW CREATE TABLE chat_sessions"),
            ("SHOW FULL COLUMNS FROM chat_messages", "SHOW CREATE TABLE chat_messages"),
            ("SHOW FULL COLUMNS FROM chat_generation_gates", "SHOW CREATE TABLE chat_generation_gates"),
        ]
        for column_statement, definition_statement in statements:
            columns = connection.execute(text(column_statement))
            assert all(column["Comment"] for column in columns.mappings())
            definition = connection.execute(text(definition_statement)).one()[1]
            assert "COMMENT=" in definition
            assert "FOREIGN KEY" not in definition


# 做什么：让两个真实数据库连接同时竞争同一会话
# 入参：会话工厂、会话编号和同步屏障
# 返回：获得占用返回 accepted，另一个必须返回 busy
def competing_request(sessions, session_id, barrier):
    barrier.wait()
    try:
        prepare_turn(sessions, 1, session_id, str(uuid4()), "并发问题")
        return "accepted"
    except ChatBusyError:
        return "busy"


# 做什么：证明 MySQL 行锁能跨连接防止同一会话重复生成
# 入参：独立 MySQL 会话工厂
# 返回：无；两边同时成功或死锁均失败
def test_mysql_serializes_concurrent_generation(mysql_sessions):
    chat_session = create_session(mysql_sessions, 1)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(competing_request, mysql_sessions, chat_session.id, barrier)
        second = executor.submit(competing_request, mysql_sessions, chat_session.id, barrier)
        outcomes = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(outcomes) == ["accepted", "busy"]


# 做什么：让不同连接同时申请不同会话的全站额度
# 入参：会话工厂、会话编号和同步屏障
# 返回：获得占用返回 accepted，满额返回 full
def competing_capacity_request(sessions, session_id, barrier):
    barrier.wait()
    try:
        prepare_turn(sessions, 1, session_id, str(uuid4()), "不同会话并行")
        return "accepted"
    except ChatCapacityError:
        return "full"


# 做什么：证明不同连接池同时申请也只能放行剩余名额
# 入参：隔离 MySQL 工厂和配置替换工具
# 返回：无；超过全站上限或死锁时失败
def test_mysql_capacity_is_atomic_across_pools(mysql_sessions, monkeypatch):
    with mysql_sessions() as db_session:
        occupied = db_session.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.active_request_id.is_not(None)))
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", occupied + 3)
    other_engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    other_sessions = sessionmaker(bind=other_engine, expire_on_commit=False)
    try:
        outcomes = run_capacity_race(mysql_sessions, other_sessions)
        assert outcomes.count("accepted") == 3
        assert outcomes.count("full") == 5
    finally:
        other_engine.dispose()


# 做什么：通过两个独立连接池发起八个同时竞争的请求
# 入参：两套指向同一隔离 MySQL 的工厂
# 返回：各请求结果；超时或数据库异常使测试失败
def run_capacity_race(first_sessions, second_sessions):
    session_ids = []
    for request_number in range(8):
        session_ids.append(create_session(first_sessions, 1).id)
    barrier = Barrier(len(session_ids))
    futures = []
    with ThreadPoolExecutor(max_workers=len(session_ids)) as executor:
        for request_number, session_id in enumerate(session_ids):
            sessions = first_sessions if request_number % 2 == 0 else second_sessions
            futures.append(executor.submit(competing_capacity_request, sessions, session_id, barrier))
        return [future.result(timeout=15) for future in futures]

