"""用 SQLite 验证接口守卫，只编译 MySQL 查询文本。"""
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import mysql

from app.core.config import get_settings
from app.rag import retrieval
from tests.test_knowledge import (
    DELETED_BASE_ID,
    KNOWLEDGE_BASE_URL,
    MISSING_BASE_ID,
    OTHER_BASE_ID,
    OWNER_BASE_ID,
    OWNER_ID,
    document_bases,
    knowledge_client,
    knowledge_sessions,
    owner_headers,
)

SEARCH_URL = f"{KNOWLEDGE_BASE_URL}/{OWNER_BASE_ID}/search"
SEARCH_TERM = "知识库切片"


# 做什么：记录搜索调用并返回可控的结果，隔离数据库相关的全文查询
# 入参：pytest 的临时替换工具
# 返回：默认返回空列表的搜索替身
@pytest.fixture
def search_stub(monkeypatch: pytest.MonkeyPatch) -> Mock:
    search = Mock(return_value=[])
    monkeypatch.setattr(retrieval, "search_chunks", search)
    return search


# 做什么：验证搜索沿用登录依赖
# 入参：隔离 HTTP 客户端与搜索替身
# 返回：无；未返回 401/1002 或进入搜索时断言失败
def test_search_requires_login(knowledge_client, search_stub) -> None:
    response = knowledge_client.post(SEARCH_URL, json={"query": SEARCH_TERM})
    assert response.status_code == 401 and response.json()["code"] == 1002
    search_stub.assert_not_called()


# 做什么：拒绝缺少查询词、长度越界或条数越界的请求
# 入参：隔离客户端、本人请求头、测试库、搜索替身和非法请求体
# 返回：无；未返回 422/1000 或进入搜索时断言失败
@pytest.mark.parametrize("payload", [
    {},
    {"query": ""},
    {"query": " 字\t"},
    {"query": "字" * 201},
    {"query": SEARCH_TERM, "top_k": 0},
    {"query": SEARCH_TERM, "top_k": 21},
])
def test_search_rejects_invalid_params(knowledge_client, owner_headers, document_bases, search_stub, payload) -> None:
    response = knowledge_client.post(SEARCH_URL, json=payload, headers=owner_headers)
    assert response.status_code == 422 and response.json()["code"] == 1000
    search_stub.assert_not_called()


# 做什么：确认不可见的个人库在执行切片查询前统一拒绝
# 入参：隔离客户端、本人请求头、测试库、搜索替身和不可见的库编号
# 返回：无；未返回 404/4001 或进入搜索时断言失败
@pytest.mark.parametrize("base_id", [OTHER_BASE_ID, DELETED_BASE_ID, MISSING_BASE_ID])
def test_search_hides_inaccessible_bases(
    knowledge_client, owner_headers, document_bases, search_stub, base_id
) -> None:
    response = knowledge_client.post(
        f"{KNOWLEDGE_BASE_URL}/{base_id}/search", json={"query": SEARCH_TERM}, headers=owner_headers
    )
    assert response.status_code == 404 and response.json()["code"] == 4001
    search_stub.assert_not_called()


# 做什么：核对 MySQL SQL 的评分、过滤、排序与参数绑定
# 入参：无，采用固定的库编号、用户编号与查询词
# 返回：无；SQL 结构或绑定参数不符合预期时断言失败
def test_mysql_search_sql_uses_natural_language_and_bound_parameters() -> None:
    statement = retrieval.build_search_statement(OWNER_ID, OWNER_BASE_ID, SEARCH_TERM, 5)
    compiled = statement.compile(dialect=mysql.dialect())
    sql = " ".join(str(compiled).split())
    assert sql.count("MATCH (knowledge_chunks.content) AGAINST (%s IN NATURAL LANGUAGE MODE)") == 2
    assert "WHERE knowledge_chunks.knowledge_base_id = %s" in sql
    assert "AND knowledge_chunks.owner_id = %s AND knowledge_chunks.is_deleted = %s" in sql
    assert "ORDER BY score DESC, knowledge_chunks.id ASC LIMIT %s" in sql
    assert SEARCH_TERM not in sql
    assert compiled.positiontup == [
        "term", "knowledge_base_id_1", "owner_id_1", "is_deleted_1", "term", "top_k",
    ]
    assert compiled.params == {
        "term": SEARCH_TERM, "knowledge_base_id_1": OWNER_BASE_ID,
        "owner_id_1": OWNER_ID, "is_deleted_1": 0, "top_k": 5,
    }


# 做什么：确认查询词先去空白，默认返回最多五条，并输出命中公开字段
# 入参：隔离客户端、本人请求头、测试库和搜索替身
# 返回：无；参数传递或响应字段不符时断言失败
def test_search_returns_public_chunk_fields(knowledge_client, owner_headers, document_bases, search_stub) -> None:
    chunk = {
        "chunk_id": 8, "document_id": 3, "heading_path": "知识库说明 > 第2段",
        "content": SEARCH_TERM, "char_start": 12, "char_end": 16, "score": 0.25,
    }
    search_stub.return_value = [chunk]
    response = knowledge_client.post(SEARCH_URL, json={"query": f"  {SEARCH_TERM}\t"}, headers=owner_headers)
    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "成功", "data": {"items": [chunk], "candidates": 1, "dropped": 0}}
    assert search_stub.call_args.args[1:] == (OWNER_ID, OWNER_BASE_ID, SEARCH_TERM, 5)


# 做什么：确认无命中返回空列表，并接受去空白后的合法参数上限
# 入参：隔离客户端、本人请求头、测试库和返回空列表的搜索替身
# 返回：无；空结果响应或有效参数传递不符时断言失败
def test_search_returns_empty_items_without_matches(knowledge_client, owner_headers, document_bases, search_stub) -> None:
    search_term = "字" * 200
    payload = {"query": f" {search_term} ", "top_k": 20}
    response = knowledge_client.post(SEARCH_URL, json=payload, headers=owner_headers)
    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "成功", "data": {"items": [], "candidates": 0, "dropped": 0}}
    assert search_stub.call_args.args[1:] == (OWNER_ID, OWNER_BASE_ID, search_term, 20)


# 做什么：构造具有真实偏移和评分的候选行
# 入参：切片编号、正文和全文得分
# 返回：搜索接口所需的一条完整候选记录
def _scored_chunk(chunk_id: int, content: str, score: float) -> dict:
    return {
        "chunk_id": chunk_id, "document_id": chunk_id, "heading_path": "资料 > 第1段",
        "content": content, "char_start": 0, "char_end": len(content), "score": score,
    }


# 做什么：验证默认阈值会排除仅共享一个二元组的候选
# 入参：隔离客户端、本人库及返回两条模拟候选的搜索替身
# 返回：无；噪声仍返回或 SQL 未多取候选时断言失败
def test_search_discards_shared_bigram_noise(knowledge_client, owner_headers, document_bases, search_stub) -> None:
    relevant = _scored_chunk(1, "会话锁保护生成占用", 2.17)
    noise = _scored_chunk(2, "会话列表展示最近记录", 0.54)
    search_stub.return_value = [relevant, noise]
    response = knowledge_client.post(SEARCH_URL, json={"query": "会话锁"}, headers=owner_headers)
    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "成功", "data": {"items": [relevant], "candidates": 2, "dropped": 1}}
    assert search_stub.call_args.kwargs == {"candidate_limit": 15}


# 做什么：确认每条候选低于显式绝对下限时仍返回成功
# 入参：隔离客户端、本人库、低分候选和配置替身
# 返回：无；未返回 200 和空 items 时断言失败
def test_search_returns_empty_items_below_absolute_floor(
    knowledge_client, owner_headers, document_bases, search_stub, monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "search_min_score_absolute", 0.05)
    search_stub.return_value = [_scored_chunk(1, SEARCH_TERM, 1.32e-08), _scored_chunk(2, "资料", 0.01)]
    response = knowledge_client.post(SEARCH_URL, json={"query": SEARCH_TERM}, headers=owner_headers)
    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "成功", "data": {"items": [], "candidates": 2, "dropped": 2}}


# 做什么：验证 SQL 候选数的最小值和最大值，结果仍按 top_k 截断
# 入参：隔离客户端、本人库、搜索替身及请求与候选条数
# 返回：无；候选边界或接口条数不符时断言失败
@pytest.mark.parametrize(("top_k", "candidate_limit"), [(1, 10), (20, 40)])
def test_search_bounds_candidate_limit(
    knowledge_client, owner_headers, document_bases, search_stub, top_k, candidate_limit,
) -> None:
    search_stub.return_value = [_scored_chunk(index, SEARCH_TERM, 1.0) for index in range(candidate_limit)]
    response = knowledge_client.post(SEARCH_URL, json={"query": SEARCH_TERM, "top_k": top_k}, headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["data"]["items"] == search_stub.return_value[:top_k]
    assert search_stub.call_args.kwargs == {"candidate_limit": candidate_limit}


# 做什么：核对部分保留、全丢弃、零候选和 top_k 截断的接口计数不变量
# 入参：隔离客户端、本人库、搜索替身及预设候选和阈值
# 返回：无；返回内容、候选数、丢弃数或查询次数不符时断言失败
@pytest.mark.parametrize(("scores", "absolute_floor", "top_k", "kept_count"), [
    ((2.17, 0.54), 0.0, 5, 1),
    ((0.02, 0.01), 0.05, 5, 0),
    ((), 0.0, 5, 0),
    ((1.0, 1.0, 1.0), 0.0, 2, 2),
])
def test_search_candidate_counts_balance(
    knowledge_client, owner_headers, document_bases, search_stub, monkeypatch,
    scores, absolute_floor, top_k, kept_count,
) -> None:
    monkeypatch.setattr(get_settings(), "search_min_score_absolute", absolute_floor)
    candidates = [_scored_chunk(index, SEARCH_TERM, score) for index, score in enumerate(scores, 1)]
    search_stub.return_value = candidates
    response = knowledge_client.post(SEARCH_URL, json={"query": SEARCH_TERM, "top_k": top_k}, headers=owner_headers)
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["items"] == candidates[:kept_count]
    assert result["candidates"] == len(candidates)
    assert result["dropped"] == len(candidates) - kept_count
    assert result["candidates"] == len(result["items"]) + result["dropped"]
    search_stub.assert_called_once()
