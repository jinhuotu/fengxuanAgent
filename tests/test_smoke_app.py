"""经应用接口走真实 MySQL 链路、并连外部模型的冒烟测试：默认不跑。准备工作和三条硬规矩见 tests/smoke_support.py。"""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import create_access_token
from app.llm.client import stream_model
from app.main import app
from app.models.knowledge import KnowledgeChunk
from app.rag import retrieval, vector_index
from tests.smoke_support import SMOKE_USER_ID, mysql_sessions, throwaway_collection  # noqa: F401

pytestmark = pytest.mark.smoke

SMOKE_BASE_NAME = "冒烟验证库"
SMOKE_DOCUMENT_TITLE = "冒烟文档"
PROBE_TERM = "星槎计划"
PROBE_QUERY = "星槎计划的验收标准"
SEARCH_TOP_K = 3
# 切分器会把短段落合并到 400-600 字一段，所以正文要写到二十多段才能切出多段
PARAGRAPH_REPEAT_COUNT = 24
MODEL_TIMEOUT_SECONDS = 60
ENV_MODEL_API_KEY = "LLM_API_KEY"
ENV_MODEL_BASE_URL = "LLM_BASE_URL"
ENV_MODEL_NAME = "LLM_MODEL"
MODEL_PROBE_QUESTION = "请只回复两个字：收到"
MODEL_FINISH_REASON = "stop"
SMOKE_PARAGRAPHS = [
    "星槎计划的验收标准包含三项：检索命中率、回答引用率和首字延迟。三项都达标才算通过，缺一项都要重新评审。",
    "青竹巷仓库每天上午九点开门，下午五点半停止收货。节假日照常收货，但需要提前一天在系统里预约时段。",
    "季度巡检表需要填写设备编号、巡检人和异常说明。发现异常的当天就要录入，并在三个工作日内补齐处理结果。",
    "新员工入职第一天领取门禁卡和电脑，第三天完成信息安全培训。培训未通过的同事要重新预约，直到通过为止。",
    "图书馆地下二层的密集书库只对教师和研究生开放，开放时间是工作日下午两点到五点，周末不对外开放。",
    "报销单据要在消费后三十天内提交，逾期系统不再受理。差旅报销需要附上行程单，餐饮报销需要写明招待事由。",
    "气象观测站每天早晚各放一次探空气球，气球携带的仪器会把高空的温度、湿度和气压传回地面接收设备。",
]


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
    assert finish_reason == MODEL_FINISH_REASON, f"模型没有正常结束：{finish_reason}"
