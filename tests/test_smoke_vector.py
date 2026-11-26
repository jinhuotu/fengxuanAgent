"""连真实向量化模型和真实 Qdrant 的冒烟测试：默认不跑。准备工作和三条硬规矩见 tests/smoke_support.py。"""
import math
import os
import subprocess
import sys
import warnings

import pytest

from app.core.config import get_settings
from app.models.knowledge import KnowledgeChunk
from app.rag import vector_index
from tests.smoke_support import PROJECT_ROOT, throwaway_collection  # noqa: F401

pytestmark = pytest.mark.smoke

SUBPROCESS_TIMEOUT_SECONDS = 180
STDERR_TAIL_LENGTH = 1500
VECTOR_SEARCH_LIMIT = 5
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
