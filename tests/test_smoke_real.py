"""连真实依赖的冒烟测试：默认不跑，发版前手动执行。

新手注意：默认的 pytest 只连假依赖（内存数据库、假模型），所以有些代码平时从来没被真正执行过：
本机的向量化模型和 Qdrant、MySQL 的全文检索 SQL、应用启动流程、外部大模型。
这里把这条真实链路走一遍。为了不碰正式数据和密钥，有三条硬规矩：
1. 只用一次性的集合名（smoke_ 开头，用完即删），绝不写 fengxuan_chunks；
2. 数据库只允许连 chat_verify 隔离库，口令是测试专用值，并且用完整迁移链建表；
3. 不读取 .env。模型密钥只从当前终端的环境变量取，没给就跳过模型那一条。

运行方法见 docs/commands.md 的"真实依赖验证"一节。
"""
import asyncio
import math
import os
import subprocess
import sys
import warnings
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.llm.client import stream_model
from app.main import app
from app.models.knowledge import KnowledgeChunk
from app.models.user import User
from app.rag import retrieval, vector_index

pytestmark = pytest.mark.smoke

PROJECT_ROOT = Path(__file__).parents[1]
ISOLATED_DATABASE_NAME = "chat_verify"
ISOLATED_DATABASE_PASSWORD = "chat-isolated-only"
REAL_COLLECTION = "fengxuan_chunks"
FULLTEXT_INDEX_NAME = "ft_knowledge_chunks_content"
FULLTEXT_INDEX_MARKER = f"FULLTEXT KEY `{FULLTEXT_INDEX_NAME}`"
NGRAM_PARSER_MARKER = "WITH PARSER `ngram`"
SMOKE_USER_ID = 1
SMOKE_BASE_NAME = "冒烟验证库"
SMOKE_DOCUMENT_TITLE = "冒烟文档"
PROBE_TERM = "星槎计划"
PROBE_QUERY = "星槎计划的验收标准"
SEARCH_TOP_K = 3
VECTOR_SEARCH_LIMIT = 5
# 切分器会把短段落合并到 400-600 字一段，所以正文要写到二十多段才能切出多段
PARAGRAPH_REPEAT_COUNT = 24
SUBPROCESS_TIMEOUT_SECONDS = 180
MODEL_TIMEOUT_SECONDS = 60
STDERR_TAIL_LENGTH = 1500
ENV_MODEL_API_KEY = "LLM_API_KEY"
ENV_MODEL_BASE_URL = "LLM_BASE_URL"
ENV_MODEL_NAME = "LLM_MODEL"
MODEL_PROBE_QUESTION = "请只回复两个字：收到"
COLD_MS = "cold_ms"
WARM_MAX_MS = "warm_max_ms"
DIMENSION = "dim"

# 一组内容互不相近的切片：第一段讲请假审批，后两段讲食堂和报销
PASSAGES = [
    "员工申请年假需要在系统里提前三天提交，由直属主管审批。",
    "食堂周三供应牛肉面，晚餐从下午五点半开始。",
    "报销单据要在消费后三十天内提交，逾期系统不再受理。",
]
# 另一个文档放一段不相干的话：如果内容和第一个文档相同，得分会打平，谁排第一就是随机的
OTHER_PASSAGES = ["会议室预约需要提前一天在前台登记，超时未到自动释放。"]
LEAVE_QUERY = "请假审批要提前多久提交"
# 直接写向量集合时用的归属信息：都是正常业务不会用到的编号，避免和真实数据撞上
SMOKE_OWNER_ID = 9_900_001
SMOKE_BASE_ID = 9_900_002
SMOKE_DOCUMENT_ID = 9_900_003
SMOKE_OTHER_DOCUMENT_ID = 9_900_004
SMOKE_CHUNK_ID_START = 9_900_011
OTHER_CHUNK_ID_OFFSET = 100
SMOKE_PARAGRAPHS = [
    "星槎计划的验收标准包含三项：检索命中率、回答引用率和首字延迟。三项都达标才算通过，缺一项都要重新评审。",
    "青竹巷仓库每天上午九点开门，下午五点半停止收货。节假日照常收货，但需要提前一天在系统里预约时段。",
    "季度巡检表需要填写设备编号、巡检人和异常说明。发现异常的当天就要录入，并在三个工作日内补齐处理结果。",
    "新员工入职第一天领取门禁卡和电脑，第三天完成信息安全培训。培训未通过的同事要重新预约，直到通过为止。",
    "图书馆地下二层的密集书库只对教师和研究生开放，开放时间是工作日下午两点到五点，周末不对外开放。",
    "报销单据要在消费后三十天内提交，逾期系统不再受理。差旅报销需要附上行程单，餐饮报销需要写明招待事由。",
    "气象观测站每天早晚各放一次探空气球，气球携带的仪器会把高空的温度、湿度和气压传回地面接收设备。",
]

# 在全新的 Python 进程里加载模型并计时，模拟"服务刚重启后的第一次检索"。
# 最后输出一行"TIMING 冷启动毫秒 常驻最慢毫秒 维度"，由父进程解析。
COLD_START_PROBE = '''
import sys
from time import perf_counter
sys.path.insert(0, __ROOT__)
started = perf_counter()
from app.rag import vector_index
first = vector_index.embed_query("知识库第一次检索")
cold_ms = (perf_counter() - started) * 1000
warm = []
for _ in range(5):
    moment = perf_counter()
    vector_index.embed_query("知识库再次检索")
    warm.append((perf_counter() - moment) * 1000)
sys.stdout.write("TIMING " + str(cold_ms) + " " + str(max(warm)) + " " + str(len(first)))
'''


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


# 做什么：在全新进程里加载向量化模型，取回冷启动耗时、常驻后的最慢耗时和向量维度
# 入参：无
# 返回：含三个测量值的字典；子进程启动失败或没有输出测量结果时失败
def measure_model_timing_in_fresh_process():
    probe = COLD_START_PROBE.replace("__ROOT__", repr(str(PROJECT_ROOT)))
    process = subprocess.run(
        [sys.executable, "-c", probe], cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert process.returncode == 0, f"子进程加载模型失败：\n{process.stderr[-STDERR_TAIL_LENGTH:]}"
    for line in process.stdout.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "TIMING":
            return {COLD_MS: float(parts[1]), WARM_MAX_MS: float(parts[2]), DIMENSION: int(parts[3])}
    raise AssertionError(f"子进程没有输出测量结果：\n{process.stdout[-STDERR_TAIL_LENGTH:]}")


# 做什么：冷启动达到或超过检索时限时给出告警，提示重启后的第一次提问可能不带资料
# 入参：三个测量值和检索时限（毫秒）
# 返回：无
def warn_if_cold_start_exceeds_budget(measured, timeout_ms):
    if measured[COLD_MS] < timeout_ms:
        return
    warnings.warn(
        f"模型冷启动 {measured[COLD_MS]:.0f}ms 已达到或超过检索时限 {timeout_ms}ms"
        f"（常驻后最慢 {measured[WARM_MAX_MS]:.0f}ms）：服务重启后的第一次提问可能来不及带上资料",
        UserWarning, stacklevel=1,
    )


# 做什么：在全新进程里实测模型冷启动和常驻后的单次耗时，和检索时限对比
# 入参：无；时限读配置里的 chat_rag_timeout_ms
# 返回：无；模型加载失败、维度不符或常驻耗时超过时限时失败；冷启动超过时限只告警
def test_embedding_model_loads_and_meets_retrieval_budget():
    measured = measure_model_timing_in_fresh_process()
    settings = get_settings()
    assert measured[DIMENSION] == settings.embedding_dim, "模型输出维度与配置 embedding_dim 不一致"
    assert measured[WARM_MAX_MS] < settings.chat_rag_timeout_ms, "模型常驻后的单次耗时已超过检索时限"
    warn_if_cold_start_exceeds_budget(measured, settings.chat_rag_timeout_ms)


# 做什么：计算两条向量的余弦相似度
# 入参：等长的两条向量
# 返回：-1 到 1 之间的相似度
def cosine(first, second):
    dot = 0.0
    first_norm = 0.0
    second_norm = 0.0
    for left, right in zip(first, second, strict=True):
        dot += left * right
        first_norm += left * left
        second_norm += right * right
    return dot / math.sqrt(first_norm) / math.sqrt(second_norm)


# 做什么：确认问"请假审批"时，最接近的是讲请假的那段，而不是食堂或报销
# 入参：问题向量和三段正文各自的向量
# 返回：无；讲请假的切片没有明显最接近时失败
def assert_leave_passage_is_closest(query_vector, passage_vectors):
    similarities = [cosine(query_vector, passage_vector) for passage_vector in passage_vectors]
    assert similarities[0] > similarities[1] and similarities[0] > similarities[2], (
        f"问'请假审批'却没有最接近讲请假的切片：{similarities}"
    )


# 做什么：把几段正文包装成可写入向量集合的切片对象
# 入参：文档编号、切片编号起点和正文列表
# 返回：切片列表；不经过数据库，归属信息由常量指定
def build_chunks(document_id, id_start, passages):
    return [
        KnowledgeChunk(
            id=id_start + index, knowledge_base_id=SMOKE_BASE_ID, document_id=document_id,
            owner_id=SMOKE_OWNER_ID, chunk_index=index, heading_path="", content=passage,
            char_start=0, char_end=len(passage),
        )
        for index, passage in enumerate(passages)
    ]


# 做什么：把两个冒烟文档写入向量集合，其中第一个文档重复写一遍以验证覆盖
# 入参：无；集合由 throwaway_collection 指定
# 返回：无；写入条数不对或重复写入后点数翻倍时失败
def write_smoke_documents():
    chunks = build_chunks(SMOKE_DOCUMENT_ID, SMOKE_CHUNK_ID_START, PASSAGES)
    other_chunks = build_chunks(SMOKE_OTHER_DOCUMENT_ID, SMOKE_CHUNK_ID_START + OTHER_CHUNK_ID_OFFSET,
                                OTHER_PASSAGES)
    expected_points = len(PASSAGES) + len(OTHER_PASSAGES)
    written = vector_index.upsert_document_chunks(
        None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_DOCUMENT_ID, chunks)
    assert written == len(PASSAGES), "第一个文档写入的向量点数不对"
    written = vector_index.upsert_document_chunks(
        None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_OTHER_DOCUMENT_ID, other_chunks)
    assert written == len(OTHER_PASSAGES), "第二个文档写入的向量点数不对"
    # 重复写入同一个文档：按切片编号覆盖，点数不能翻倍
    vector_index.upsert_document_chunks(None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_DOCUMENT_ID, chunks)
    assert vector_index.count_points() == expected_points, "重复同步同一文档后点数变多了"


# 做什么：用真实模型和真实 Qdrant 验证语义排序，以及别的用户、别的库查不到这些点
# 入参：一次性集合
# 返回：无；语义排序错误或越权可见时失败
def test_vector_search_ranks_related_passage_first(throwaway_collection):
    query_vector = vector_index.embed_query(LEAVE_QUERY)
    passage_vectors = vector_index.embed_passages(PASSAGES)
    assert_leave_passage_is_closest(query_vector, passage_vectors)
    write_smoke_documents()

    hits = vector_index.search_vectors(SMOKE_OWNER_ID, SMOKE_BASE_ID, query_vector, VECTOR_SEARCH_LIMIT)
    assert hits[0].chunk_id == SMOKE_CHUNK_ID_START, f"最相似的不是讲请假的切片：{hits}"
    assert hits[0].score > hits[1].score, f"第一名没有明显领先，说明排序不可靠：{hits}"
    other_owner_hits = vector_index.search_vectors(SMOKE_OWNER_ID + 1, SMOKE_BASE_ID, query_vector,
                                                   VECTOR_SEARCH_LIMIT)
    assert other_owner_hits == [], "别的用户查到了"
    other_base_hits = vector_index.search_vectors(SMOKE_OWNER_ID, SMOKE_BASE_ID + 1, query_vector,
                                                  VECTOR_SEARCH_LIMIT)
    assert other_base_hits == [], "别的库查到了"


# 做什么：验证向量点能按文档删除，也能按整个库删除，删完没有残留
# 入参：一次性集合
# 返回：无；删除后点数不符时失败
def test_vector_points_are_removed_with_document_and_base(throwaway_collection):
    write_smoke_documents()
    vector_index.delete_document(SMOKE_OTHER_DOCUMENT_ID)
    assert vector_index.count_points() == len(PASSAGES), "按文档删除后点数不对"
    vector_index.delete_knowledge_base(SMOKE_BASE_ID)
    assert vector_index.count_points() == 0, "按库删除后仍有残留"


# 做什么：在隔离库上从头跑到最新的迁移
# 入参：无；数据库地址来自隔离配置，只可能指向 chat_verify
# 返回：无；任一步迁移失败时原样抛出
def run_migrations_to_head():
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
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


# 做什么：拼出冒烟测试用的文档正文，把七段话重复编号到二十多段，确保能切出多段
# 入参：无
# 返回：用空行分隔的正文
def build_smoke_content():
    sections = []
    for number in range(1, PARAGRAPH_REPEAT_COUNT + 1):
        sections.append(f"第{number}则 {SMOKE_PARAGRAPHS[(number - 1) % len(SMOKE_PARAGRAPHS)]}")
    return "\n\n".join(sections)


# 做什么：通过上传接口在指定知识库里创建冒烟文档
# 入参：测试客户端、登录请求头和知识库编号
# 返回：新文档的编号；上传失败时失败
def upload_smoke_document(client, headers, base_id):
    response = client.post(f"/knowledge-bases/{base_id}/documents",
                           json={"title": SMOKE_DOCUMENT_TITLE, "content": build_smoke_content()},
                           headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


# 做什么：统计一篇文档已删除或未删除的切片数
# 入参：隔离库会话工厂、文档编号，以及是否只数已删除的
# 返回：切片数
def count_document_chunks(sessions, document_id, deleted):
    condition = KnowledgeChunk.is_deleted != 0 if deleted else KnowledgeChunk.is_deleted == 0
    statement = select(func.count()).select_from(KnowledgeChunk).where(
        KnowledgeChunk.document_id == document_id, condition)
    with sessions() as db_session:
        return db_session.scalar(statement)


# 做什么：确认上传后两路索引都收到了这篇文档：MySQL 全文检索能命中，向量点数等于切片数
# 入参：隔离库会话工厂、知识库编号和文档编号
# 返回：无；切片太少、全文检索无命中或向量点数不符时失败
def assert_both_indexes_received_document(sessions, base_id, document_id):
    chunk_count = count_document_chunks(sessions, document_id, deleted=False)
    assert chunk_count > 1, f"文档只切出 {chunk_count} 段，没有走到多段同步的路径"
    with sessions() as db_session:
        rows = retrieval.search_chunks(db_session, SMOKE_USER_ID, base_id, PROBE_TERM, SEARCH_TOP_K)
    scores = [row["score"] for row in rows]
    assert rows and min(scores) > 0, "MySQL 全文检索没有命中"
    assert vector_index.count_points() == chunk_count, "上传后向量点数与切片数不一致"


# 做什么：调用检索接口并返回命中列表
# 入参：测试客户端、登录请求头、知识库编号和查询词
# 返回：命中切片列表；接口报错时失败
def search_smoke_base(client, headers, base_id, query):
    response = client.post(f"/knowledge-bases/{base_id}/search",
                           json={"query": query, "top_k": SEARCH_TOP_K}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


# 做什么：确认删除文档后三处都查不到它：向量点、未删除的切片、检索接口
# 入参：隔离库会话工厂、测试客户端、登录请求头、知识库编号和文档编号
# 返回：无；任一处仍有残留时失败
def assert_document_is_gone_everywhere(sessions, client, headers, base_id, document_id):
    assert vector_index.count_points() == 0, "删除文档后向量点没有清掉"
    alive_count = count_document_chunks(sessions, document_id, deleted=False)
    deleted_count = count_document_chunks(sessions, document_id, deleted=True)
    assert alive_count == 0 and deleted_count > 1, "删除文档后切片没有全部标记为已删除"
    assert search_smoke_base(client, headers, base_id, PROBE_TERM) == [], "删除文档后仍然能检索到它的内容"


# 做什么：通过应用接口在真实 MySQL 上走完"建库、上传、混合检索、删除"，并确认向量同步到位
# 入参：隔离库会话工厂、一次性集合、配置替换工具
# 返回：无；全文检索无命中、混合检索不返回资料或删除后仍有残留时失败
def test_mysql_upload_search_delete_chain(mysql_sessions, throwaway_collection, monkeypatch):
    monkeypatch.setattr(get_settings(), "vector_retrieval_mode", "hybrid")
    headers = {"Authorization": f"Bearer {create_access_token(SMOKE_USER_ID)}"}
    with TestClient(app) as client:
        assert client.get("/health").json()["data"]["status"] == "ok"
        base_response = client.post("/knowledge-bases", json={"name": SMOKE_BASE_NAME}, headers=headers)
        assert base_response.status_code == 200, base_response.text
        base_id = base_response.json()["data"]["id"]
        try:
            document_id = upload_smoke_document(client, headers, base_id)
            assert_both_indexes_received_document(mysql_sessions, base_id, document_id)
            top_items = search_smoke_base(client, headers, base_id, PROBE_QUERY)
            assert top_items and PROBE_TERM in top_items[0]["content"], (
                f"混合检索没有把相关资料排在第一位：{top_items}"
            )
            document_path = f"/knowledge-bases/{base_id}/documents/{document_id}"
            assert client.delete(document_path, headers=headers).status_code == 200
            assert_document_is_gone_everywhere(mysql_sessions, client, headers, base_id, document_id)
        finally:
            client.delete(f"/knowledge-bases/{base_id}", headers=headers)


# 做什么：把当前终端里的模型配置写进测试用的配置对象，地址和模型名没给就沿用默认值
# 入参：pytest 提供的配置替换工具
# 返回：无
def apply_model_environment(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(os.environ[ENV_MODEL_API_KEY]))
    base_url = os.environ.get(ENV_MODEL_BASE_URL, "")
    model_name = os.environ.get(ENV_MODEL_NAME, "")
    if base_url:
        monkeypatch.setattr(settings, "llm_base_url", base_url)
    if model_name:
        monkeypatch.setattr(settings, "llm_model", model_name)


# 做什么：真实调用一次模型，把收到的片段全部收集起来
# 入参：无
# 返回：模型片段列表；网络或协议错误原样抛出
async def stream_real_answer():
    chunks = []
    async for chunk in stream_model([], MODEL_PROBE_QUESTION):
        chunks.append(chunk)
    return chunks


# 做什么：把模型片段流拼成完整回答，并记下最后一个结束原因
# 入参：模型片段列表
# 返回：回答文本和结束原因；模型没有给出结束原因时为 None
def collect_answer(chunks):
    content = ""
    finish_reason = None
    for chunk in chunks:
        for choice in chunk.choices:
            content += choice.delta.content or ""
            if choice.finish_reason:
                finish_reason = choice.finish_reason
    return content, finish_reason


# 做什么：确认密钥、地址、模型名可用，且流式协议能被正常解析
# 入参：配置替换工具
# 返回：无；没有内容或没有正常结束标志时失败；当前终端没有提供密钥时跳过
def test_real_model_streams_answer(monkeypatch):
    if not os.environ.get(ENV_MODEL_API_KEY, ""):
        pytest.skip(f"当前终端没有设置 {ENV_MODEL_API_KEY}，跳过模型连通性检查")
    apply_model_environment(monkeypatch)
    chunks = asyncio.run(asyncio.wait_for(stream_real_answer(), timeout=MODEL_TIMEOUT_SECONDS))
    content, finish_reason = collect_answer(chunks)
    assert content.strip(), "模型没有返回任何内容"
    assert finish_reason == "stop", f"模型没有正常结束：{finish_reason}"
