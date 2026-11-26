"""冒烟测试公用的准备与核对：隔离库、一次性向量集合。这里不放任何用例。

新手注意：默认的 pytest 只连假依赖（内存数据库、假模型），所以有些代码平时从来没被真正执行过：
本机的向量化模型和 Qdrant、MySQL 的全文检索 SQL、应用启动流程、外部大模型。
冒烟测试把这条真实链路走一遍，分成两个文件：
- tests/test_smoke_vector.py：向量化模型和 Qdrant；
- tests/test_smoke_app.py：经应用接口走 MySQL 链路，以及外部模型。
为了不碰正式数据和密钥，有三条硬规矩：
1. 只用一次性的集合名（smoke_ 开头，用完即删），绝不写 fengxuan_chunks；
2. 数据库只允许连 chat_verify 隔离库，口令是测试专用值，并且用完整迁移链建表；
3. 不读取 .env。模型密钥只从当前终端的环境变量取，没给就跳过模型那一条。

运行方法见 docs/commands.md 的"真实依赖验证"一节。
"""
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.rag import vector_index

PROJECT_ROOT = Path(__file__).parents[1]
ISOLATED_DATABASE_NAME = "chat_verify"
ISOLATED_DATABASE_PASSWORD = "chat-isolated-only"
REAL_COLLECTION = "fengxuan_chunks"
FULLTEXT_INDEX_NAME = "ft_knowledge_chunks_content"
FULLTEXT_INDEX_MARKER = f"FULLTEXT KEY `{FULLTEXT_INDEX_NAME}`"
NGRAM_PARSER_MARKER = "WITH PARSER `ngram`"
SMOKE_USER_ID = 1


# 做什么：生成一次性集合并把向量检索切到这个集合上，用完删除
# 入参：pytest 提供的配置替换工具
# 返回：集合名；集合名等于正式集合时直接失败，防止误写正式数据
@pytest.fixture
def throwaway_collection(monkeypatch):
    name = f"smoke_{uuid4().hex[:12]}"
    assert name != REAL_COLLECTION
    settings = get_settings()
    monkeypatch.setattr(settings, "vector_enabled", True)
    monkeypatch.setattr(settings, "qdrant_collection", name)
    yield name
    client = vector_index.get_client()
    if client.collection_exists(name):
        client.delete_collection(name)


# 做什么：确认隔离库要么是空库，要么是之前由迁移建到一半的库，否则给出重建命令
# 入参：隔离库连接地址
# 返回：无；库里有表却没有迁移记录时直接失败，避免报出"表已存在"这种看不懂的错
# 新手注意：tests/mysql_chat_checks.py 用 create_all 建表，不留迁移记录，和这里不能共用同一个库。
def assert_database_is_migratable(database_url):
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            tables = {row[0] for row in connection.execute(text("SHOW TABLES"))}
    finally:
        engine.dispose()
    if tables and "alembic_version" not in tables:
        pytest.fail(
            "隔离库里有表却没有迁移记录（可能是先跑过 tests/mysql_chat_checks.py）。先换成空库再运行：\n"
            "docker compose --profile verify up -d --force-recreate mysql-verify"
        )


# 做什么：在隔离库上从头跑到最新的迁移
# 入参：无；数据库地址来自隔离配置，只可能指向 chat_verify
# 返回：无；任一步迁移失败时原样抛出
def run_migrations_to_head():
    # 迁移脚本从隔离配置取连接串，pytest 继续管理日志，否则 Alembic 会禁用已有 logger。
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(alembic_config, "head")


# 做什么：找出迁移结果与模型之间对不上的地方
# 入参：隔离库的一个连接
# 返回：差异列表。全文索引只写在迁移里、模型不声明，所以 autogenerate 必然报"多出一个索引"，
#       这一项是预期的，不算问题；除此之外任何差异都说明迁移和模型对不上
def collect_unexpected_schema_differences(connection):
    differences = []
    for difference in compare_metadata(MigrationContext.configure(connection), Base.metadata):
        is_known_index = (difference[0] == "remove_index"
                          and getattr(difference[1], "name", None) == FULLTEXT_INDEX_NAME)
        if not is_known_index:
            differences.append(difference)
    return differences


# 做什么：读取一张表的建表语句
# 入参：数据库引擎和表名
# 返回：建表语句字符串
def read_create_table_sql(engine, table_name):
    with engine.connect() as connection:
        return connection.execute(text(f"SHOW CREATE TABLE {table_name}")).one()[1]


# 做什么：确保隔离库里有冒烟测试登录用的用户，已存在则不重复创建
# 入参：隔离库会话工厂
# 返回：无
def seed_smoke_user(sessions):
    with sessions.begin() as db_session:
        if db_session.get(User, SMOKE_USER_ID) is None:
            db_session.add(User(id=SMOKE_USER_ID, username="mysql_owner", password_hash="not-used"))


# 做什么：在 chat_verify 隔离库上跑完整迁移链，核对结构并准备一个测试用户
# 入参：无；库名和口令必须是测试专用值，否则跳过或直接失败
# 返回：MySQL 会话工厂；迁移跑不通、结构与模型不一致或全文索引缺失时失败
# 新手注意：这里必须走迁移而不是 create_all。全文索引带 ngram 分词，只写在迁移里，
#          create_all 建出来的表没有这个索引，检索会直接报错。
@pytest.fixture(scope="module")
def mysql_sessions():
    settings = get_settings()
    if settings.db_name != ISOLATED_DATABASE_NAME:
        pytest.skip("未指定 --chat-mysql-port，跳过真实 MySQL 链路；启动方法见 docs/commands.md")
    assert settings.db_password == ISOLATED_DATABASE_PASSWORD, "禁止使用真实库凭证"
    assert_database_is_migratable(settings.database_url)
    run_migrations_to_head()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        differences = collect_unexpected_schema_differences(connection)
    assert differences == [], f"迁移结果与模型不一致：{differences}"
    # 分词器不会出现在 information_schema 里，只能从建表语句确认"是全文索引，而且用 ngram 切中文"
    create_table = read_create_table_sql(engine, "knowledge_chunks")
    assert FULLTEXT_INDEX_MARKER in create_table, "切片表的全文索引不存在"
    assert NGRAM_PARSER_MARKER in create_table, "全文索引没有使用 ngram 分词，中文检索会不可靠"
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    seed_smoke_user(sessions)
    yield sessions
    engine.dispose()
