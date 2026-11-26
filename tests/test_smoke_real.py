"""连真实依赖的冒烟测试：默认不跑，发版前手动执行。

新手注意：默认的 pytest 只连假依赖（内存数据库、假模型），所以有些代码平时从来没被真正执行过：
本机的向量化模型和 Qdrant、MySQL 的全文检索 SQL、应用启动流程、外部大模型。
这里把这条真实链路走一遍。为了不碰正式数据，有三条硬规矩：
1. 只用一次性的集合名（smoke_ 开头，用完即删），绝不写 fengxuan_chunks；
2. 数据库只允许连 chat_verify 隔离库，口令是测试专用的假口令，并且用完整迁移链建表；
3. 模型密钥只从 .env 读 LLM_ 开头的几行，不读、也不打印任何数据库凭证。

运行方法见 docs/commands.md 的"冒烟测试"一节。
"""
import asyncio
import json
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
from app.main import app
from app.models.knowledge import KnowledgeChunk
from app.models.user import User
from app.rag import retrieval, vector_index

pytestmark = pytest.mark.smoke

PROJECT_ROOT = Path(__file__).parents[1]
REAL_COLLECTION = "fengxuan_chunks"
# 一组内容互不相近的切片：第一段讲请假审批，后两段讲食堂和报销
PASSAGES = [
    "员工申请年假需要在系统里提前三天提交，由直属主管审批。",
    "食堂周三供应牛肉面，晚餐从下午五点半开始。",
    "报销单据要在消费后三十天内提交，逾期系统不再受理。",
]
LEAVE_QUERY = "请假审批要提前多久提交"
# 直接写向量集合时用的归属信息：都是正常业务不会用到的编号，避免和真实数据撞上
SMOKE_OWNER_ID = 9_900_001
SMOKE_BASE_ID = 9_900_002
SMOKE_DOCUMENT_ID = 9_900_003
SMOKE_OTHER_DOCUMENT_ID = 9_900_004
SMOKE_CHUNK_ID_START = 9_900_011

# 在全新的 Python 进程里加载模型并计时，模拟"服务刚重启后的第一次检索"
COLD_START_PROBE = '''
import json, sys
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
print("RESULT=" + json.dumps({"cold_ms": cold_ms, "warm_max_ms": max(warm), "dim": len(first)}))
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
#          MySQL 镜像带匿名数据卷，重建时必须加 -V 才会真正换成空库。
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
            "docker compose --profile verify up -d --force-recreate -V mysql-verify"
        )


# 做什么：在 chat_verify 隔离库上跑完整迁移链，核对结构并准备一个测试用户
# 入参：无；库名和口令必须是测试专用值，否则跳过或直接失败
# 返回：MySQL 会话工厂；迁移跑不通、结构与模型不一致或全文索引缺失时失败
# 新手注意：这里必须走迁移而不是 create_all。全文索引带 ngram 分词，只写在迁移里，
#          create_all 建出来的表没有这个索引，检索会直接报错。
@pytest.fixture(scope="module")
def mysql_sessions():
    settings = get_settings()
    if settings.db_name != "chat_verify":
        pytest.skip("未指定 --chat-mysql-port，跳过真实 MySQL 链路；启动方法见 docs/commands.md")
    assert settings.db_password == "chat-isolated-only", "禁止使用真实库凭证"
    assert_database_is_migratable(settings.database_url)
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(alembic_config, "head")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        # 全文索引只写在迁移里、模型不声明，所以 autogenerate 必然报"多出一个索引"，这一项不算问题；
        # 索引是否真的建好由下面的 ngram 断言负责。除此之外任何差异都说明迁移和模型对不上。
        differences = [diff for diff in compare_metadata(MigrationContext.configure(connection), Base.metadata)
                       if not (diff[0] == "remove_index" and getattr(diff[1], "name", None) == "ft_knowledge_chunks_content")]
    assert differences == [], f"迁移结果与模型不一致：{differences}"
    with engine.connect() as connection:
        # 分词器不会出现在 information_schema 里，只能从建表语句确认"是全文索引，而且用 ngram 切中文"
        create_table = connection.execute(text("SHOW CREATE TABLE knowledge_chunks")).one()[1]
    assert "FULLTEXT KEY `ft_knowledge_chunks_content`" in create_table, "切片表的全文索引不存在"
    assert "WITH PARSER `ngram`" in create_table, "全文索引没有使用 ngram 分词，中文检索会不可靠"
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as db_session:
        if db_session.get(User, 1) is None:
            db_session.add(User(id=1, username="mysql_owner", password_hash="not-used"))
    yield sessions
    engine.dispose()


# 做什么：计算两条向量的余弦相似度
# 入参：等长的两条向量
# 返回：-1 到 1 之间的相似度
def cosine(first, second):
    dot = sum(left * right for left, right in zip(first, second, strict=True))
    norm = math.sqrt(sum(value * value for value in first)) * math.sqrt(sum(value * value for value in second))
    return dot / norm


# 做什么：把几段正文包装成可写入向量集合的切片对象
# 入参：文档编号、切片编号起点和正文列表
# 返回：切片列表；不经过数据库，归属信息由常量指定
def build_chunks(document_id, id_start, passages=PASSAGES):
    return [
        KnowledgeChunk(
            id=id_start + index, knowledge_base_id=SMOKE_BASE_ID, document_id=document_id,
            owner_id=SMOKE_OWNER_ID, chunk_index=index, heading_path="", content=passage,
            char_start=0, char_end=len(passage),
        )
        for index, passage in enumerate(passages)
    ]


# 另一个文档放一段不相干的话：如果内容和第一个文档相同，得分会打平，谁排第一就是随机的
OTHER_PASSAGES = ["会议室预约需要提前一天在前台登记，超时未到自动释放。"]


# 做什么：在全新进程里实测模型冷启动和常驻后的单次耗时，和检索时限对比
# 入参：无；时限读配置里的 chat_rag_timeout_ms
# 返回：无；模型加载失败、维度不符或常驻耗时超过时限时失败；冷启动超过时限只告警
def test_embedding_model_loads_and_meets_retrieval_budget():
    probe = COLD_START_PROBE.replace("__ROOT__", repr(str(PROJECT_ROOT)))
    process = subprocess.run(
        [sys.executable, "-c", probe], cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert process.returncode == 0, f"子进程加载模型失败：\n{process.stderr[-1500:]}"
    line = next(value for value in process.stdout.splitlines() if value.startswith("RESULT="))
    measured = json.loads(line.removeprefix("RESULT="))
    settings = get_settings()
    assert measured["dim"] == settings.embedding_dim, "模型输出维度与配置 embedding_dim 不一致"
    assert measured["warm_max_ms"] < settings.chat_rag_timeout_ms, "模型常驻后的单次耗时已超过检索时限"
    print(f"\n向量化耗时 | 冷启动 {measured['cold_ms']:.0f}ms | 常驻最慢 {measured['warm_max_ms']:.0f}ms"
          f" | 检索时限 {settings.chat_rag_timeout_ms}ms")
    if measured["cold_ms"] >= settings.chat_rag_timeout_ms:
        warnings.warn(
            f"模型冷启动 {measured['cold_ms']:.0f}ms 已达到或超过检索时限 {settings.chat_rag_timeout_ms}ms："
            "服务重启后的第一次提问可能来不及带上资料", UserWarning, stacklevel=1,
        )


# 做什么：用真实模型和真实 Qdrant 验证"写入、按相似度查、按归属过滤、删除"整条链路
# 入参：一次性集合
# 返回：无；语义排序错误、点数不符、越权可见或删除后仍有残留时失败
def test_real_embedding_and_qdrant_round_trip(throwaway_collection):
    query_vector = vector_index.embed_query(LEAVE_QUERY)
    passage_vectors = vector_index.embed_passages(PASSAGES)
    similarities = [cosine(query_vector, passage_vector) for passage_vector in passage_vectors]
    assert similarities[0] > similarities[1] and similarities[0] > similarities[2], (
        f"问'请假审批'却没有最接近讲请假的切片：{similarities}"
    )

    chunks = build_chunks(SMOKE_DOCUMENT_ID, SMOKE_CHUNK_ID_START)
    other_chunks = build_chunks(SMOKE_OTHER_DOCUMENT_ID, SMOKE_CHUNK_ID_START + 100, OTHER_PASSAGES)
    assert vector_index.upsert_document_chunks(None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_DOCUMENT_ID, chunks) == 3
    assert vector_index.upsert_document_chunks(
        None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_OTHER_DOCUMENT_ID, other_chunks) == 1
    # 重复写入同一个文档：按切片编号覆盖，点数不能翻倍
    vector_index.upsert_document_chunks(None, SMOKE_BASE_ID, SMOKE_OWNER_ID, SMOKE_DOCUMENT_ID, chunks)
    assert vector_index.count_points() == 4, "重复同步同一文档后点数变多了"

    hits = vector_index.search_vectors(SMOKE_OWNER_ID, SMOKE_BASE_ID, query_vector, 5)
    assert hits[0].chunk_id == SMOKE_CHUNK_ID_START, f"最相似的不是讲请假的切片：{hits}"
    assert hits[0].score > hits[1].score, f"第一名没有明显领先，说明排序不可靠：{hits}"
    assert vector_index.search_vectors(SMOKE_OWNER_ID + 1, SMOKE_BASE_ID, query_vector, 5) == [], "别的用户查到了"
    assert vector_index.search_vectors(SMOKE_OWNER_ID, SMOKE_BASE_ID + 1, query_vector, 5) == [], "别的库查到了"

    vector_index.delete_document(SMOKE_OTHER_DOCUMENT_ID)
    assert vector_index.count_points() == 3, "按文档删除后点数不对"
    vector_index.delete_knowledge_base(SMOKE_BASE_ID)
    assert vector_index.count_points() == 0, "按库删除后仍有残留"


# 做什么：通过应用接口在真实 MySQL 上走完"建库、上传、检索、删除"，并确认向量同步到位
# 入参：隔离库会话工厂、一次性集合、配置替换工具
# 返回：无；全文检索无命中、混合检索不返回资料或删除后向量残留时失败
def test_mysql_upload_search_delete_chain(mysql_sessions, throwaway_collection, monkeypatch):
    monkeypatch.setattr(get_settings(), "vector_retrieval_mode", "hybrid")
    headers = {"Authorization": f"Bearer {create_access_token(1)}"}
    # 切分器会把短段落合并到 400-600 字一段，所以要多写几段才能走到"一篇文档多段切片"的路径
    paragraphs = [
        "星槎计划的验收标准包含三项：检索命中率、回答引用率和首字延迟。三项都达标才算通过，缺一项都要重新评审。",
        "青竹巷仓库每天上午九点开门，下午五点半停止收货。节假日照常收货，但需要提前一天在系统里预约时段。",
        "季度巡检表需要填写设备编号、巡检人和异常说明。发现异常的当天就要录入，并在三个工作日内补齐处理结果。",
        "新员工入职第一天领取门禁卡和电脑，第三天完成信息安全培训。培训未通过的同事要重新预约，直到通过为止。",
        "图书馆地下二层的密集书库只对教师和研究生开放，开放时间是工作日下午两点到五点，周末不对外开放。",
        "报销单据要在消费后三十天内提交，逾期系统不再受理。差旅报销需要附上行程单，餐饮报销需要写明招待事由。",
        "气象观测站每天早晚各放一次探空气球，气球携带的仪器会把高空的温度、湿度和气压传回地面接收设备。",
    ]
    content = "\n\n".join(f"第{number}则 {paragraphs[(number - 1) % len(paragraphs)]}" for number in range(1, 25))
    with TestClient(app) as client:
        assert client.get("/health").json()["data"]["status"] == "ok"
        base_response = client.post("/knowledge-bases", json={"name": "冒烟验证库"}, headers=headers)
        assert base_response.status_code == 200, base_response.text
        base_id = base_response.json()["data"]["id"]
        try:
            upload = client.post(f"/knowledge-bases/{base_id}/documents",
                                 json={"title": "冒烟文档", "content": content}, headers=headers)
            assert upload.status_code == 200, upload.text
            document_id = upload.json()["data"]["id"]
            with mysql_sessions() as db_session:
                chunk_count = db_session.scalar(
                    select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
                rows = retrieval.search_chunks(db_session, 1, base_id, "星槎计划", 5)
            assert chunk_count > 1, f"文档只切出 {chunk_count} 段，没有走到多段同步的路径"
            assert rows and all(row["score"] > 0 for row in rows), "MySQL 全文检索没有命中"
            assert vector_index.count_points() == chunk_count > 0, "上传后向量点数与切片数不一致"

            search = client.post(f"/knowledge-bases/{base_id}/search",
                                 json={"query": "星槎计划的验收标准", "top_k": 3}, headers=headers)
            assert search.status_code == 200, search.text
            items = search.json()["data"]["items"]
            assert items and "星槎计划" in items[0]["content"], f"混合检索没有把相关资料排在第一位：{items}"

            assert client.delete(f"/knowledge-bases/{base_id}/documents/{document_id}", headers=headers).status_code == 200
            assert vector_index.count_points() == 0, "删除文档后向量点没有清掉"
            with mysql_sessions() as db_session:
                deleted = db_session.scalar(select(func.count()).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == document_id, KnowledgeChunk.is_deleted != 0))
            assert deleted == chunk_count, "删除文档后切片没有全部标记为已删除"
            leftovers = client.post(f"/knowledge-bases/{base_id}/search",
                                    json={"query": "星槎计划", "top_k": 3}, headers=headers).json()["data"]["items"]
            assert leftovers == [], "删除文档后仍然能检索到它的内容"
        finally:
            client.delete(f"/knowledge-bases/{base_id}", headers=headers)


# 做什么：只读取 .env 中模型相关的几行，避免把数据库凭证也读进测试
# 入参：无
# 返回：以 LLM_ 开头的配置项字典；文件不存在时为空
def read_llm_settings():
    env_path = PROJECT_ROOT / ".env"
    values = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().startswith("LLM_"):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# 做什么：调用外部模型，确认密钥、地址、模型名可用且流式协议能被正常解析
# 入参：配置替换工具
# 返回：无；没有内容、没有正常结束标志或网络异常时失败；未配置密钥时跳过
def test_real_model_streams_answer(monkeypatch):
    values = read_llm_settings()
    if not values.get("LLM_API_KEY"):
        pytest.skip(".env 里没有 LLM_API_KEY，跳过模型连通性检查")
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(values["LLM_API_KEY"]))
    for field, env_name in (("llm_base_url", "LLM_BASE_URL"), ("llm_model", "LLM_MODEL")):
        if env_name in values:
            monkeypatch.setattr(settings, field, values[env_name])

    async def collect():
        from app.llm.client import stream_model

        return [chunk async for chunk in stream_model([], "请只回复两个字：收到")]

    chunks = asyncio.run(asyncio.wait_for(collect(), timeout=60))
    content = "".join(
        choice.delta.content or "" for chunk in chunks for choice in chunk.choices if choice.delta.content
    )
    finish_reasons = [choice.finish_reason for chunk in chunks for choice in chunk.choices if choice.finish_reason]
    assert content.strip(), "模型没有返回任何内容"
    assert finish_reasons and finish_reasons[-1] == "stop", f"模型没有正常结束：{finish_reasons}"
    print(f"\n模型连通 | model={settings.llm_model} | 片段 {len(chunks)} 个 | 回答：{content.strip()[:20]}")
