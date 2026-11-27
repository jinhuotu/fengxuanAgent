"""验证个人知识库软删除、原文分页与上传索引自检。"""
from datetime import datetime
from unittest.mock import MagicMock, Mock, call

import pytest
from sqlalchemy import event, select

from app.core.knowledge import CHUNK_OVERLAP_MAX, CHUNK_OVERLAP_MIN, DocumentStatus
from app.core.security import create_access_token
from app.models.chat import ChatSession
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag import index_maintenance, retrieval
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services import knowledge, knowledge_document_store as document_store
from tests.test_knowledge import (
    DELETED,
    DOCUMENT_URL,
    KNOWLEDGE_BASE_URL,
    OTHER_BASE_ID,
    OTHER_OWNER_ID,
    OWNER_BASE_ID,
    OWNER_ID,
    SECOND_OWNER_BASE_ID,
    chunked_upload,
    document_bases,
    knowledge_client,
    knowledge_sessions,
    owner_headers,
)

BASE_URL = f"{KNOWLEDGE_BASE_URL}/{OWNER_BASE_ID}"
SEARCH_WORD = "项目索引验收"
PROBE_CONTENT = f"{SEARCH_WORD}验证上传自检是否命中"


# 做什么：通过接口在指定的库上传一篇单块文档
# 入参：测试客户端、请求头和库编号
# 返回：新文档编号；上传失败时断言失败
def upload_single_chunk(client, headers, base_id=OWNER_BASE_ID) -> int:
    response = client.post(
        f"{KNOWLEDGE_BASE_URL}/{base_id}/documents",
        json={"title": "索引测试", "content": PROBE_CONTENT}, headers=headers,
    )
    assert response.status_code == 200
    return response.json()["data"]["id"]


# 做什么：用 SQLite 读取与全文检索相同归属和软删除范围内的测试片段
# 入参：搜索使用的会话、用户、库、关键词和条数
# 返回：测试命中记录；仅替换 MySQL 的关键词匹配与评分
def search_in_sqlite(db_session, owner_id, base_id, query, top_k, *, candidate_limit=None):
    row_limit = top_k if candidate_limit is None else candidate_limit
    chunk_query = select(
        KnowledgeChunk.id.label("chunk_id"), KnowledgeChunk.document_id, KnowledgeChunk.heading_path,
        KnowledgeChunk.content, KnowledgeChunk.char_start, KnowledgeChunk.char_end,
    ).where(
        KnowledgeChunk.owner_id == owner_id, KnowledgeChunk.knowledge_base_id == base_id,
        KnowledgeChunk.is_deleted == 0, KnowledgeChunk.content.contains(query),
    ).order_by(KnowledgeChunk.id).limit(row_limit)
    return [{**chunk, "score": 1.0} for chunk in db_session.execute(chunk_query).mappings()]


# 做什么：确认删库计数、软删除和各读入口的可见性
# 入参：预建库、隔离客户端、归属请求头和 SQLite 搜索替身
# 返回：无；计数或数据库状态与契约不符时断言失败
def test_delete_base_hides_owned_content(knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch):
    document_ids = [upload_single_chunk(knowledge_client, owner_headers) for _ in range(2)]
    monkeypatch.setattr(retrieval, "search_chunks", search_in_sqlite)
    search_body = {"query": SEARCH_WORD}
    before = knowledge_client.post(f"{BASE_URL}/search", json=search_body, headers=owner_headers)
    assert len(before.json()["data"]["items"]) == 2
    response = knowledge_client.delete(BASE_URL, headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "id": OWNER_BASE_ID, "deleted_documents": 2, "deleted_chunks": 2, "cleared_sessions": 0,
    }
    listed = knowledge_client.get(KNOWLEDGE_BASE_URL, headers=owner_headers).json()["data"]["items"]
    assert [item["id"] for item in listed] == [SECOND_OWNER_BASE_ID]
    for suffix, method in [("/documents", "GET"), ("/search", "POST")]:
        request_body = search_body if method == "POST" else None
        hidden = knowledge_client.request(method, f"{BASE_URL}{suffix}", json=request_body, headers=owner_headers)
        assert (hidden.status_code, hidden.json()["code"]) == (404, 4001)
    with knowledge_sessions() as query_session:
        assert query_session.get(KnowledgeBase, OWNER_BASE_ID).is_deleted == DELETED
        documents = query_session.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id)).all()
        assert [(document.id, document.is_deleted) for document in documents] == [
            (document_id, DELETED) for document_id in document_ids
        ]
        chunks = query_session.scalars(select(KnowledgeChunk)).all()
        assert len(chunks) == 2 and all(chunk.is_deleted == DELETED for chunk in chunks)
        assert search_in_sqlite(query_session, OWNER_ID, OWNER_BASE_ID, SEARCH_WORD, 5) == []


# 做什么：确认重复删库返回同一个不可见错误
# 入参：预建库、隔离客户端及归属请求头
# 返回：无；重复调用未返回 404/4001 时断言失败
def test_delete_base_rejects_repeat(knowledge_client, owner_headers, document_bases):
    assert knowledge_client.delete(BASE_URL, headers=owner_headers).status_code == 200
    response = knowledge_client.delete(BASE_URL, headers=owner_headers)
    assert (response.status_code, response.json()["code"]) == (404, 4001)


# 做什么：确认第二个用户无法删除第一人的知识库
# 入参：预建库、隔离客户端及两人的登录凭证
# 返回：无；未隐藏他人库或本人库发生变化时断言失败
def test_delete_base_rejects_other_account(knowledge_client, owner_headers, document_bases):
    other_headers = {"Authorization": f"Bearer {create_access_token(OTHER_OWNER_ID)}"}
    response = knowledge_client.delete(BASE_URL, headers=other_headers)
    assert (response.status_code, response.json()["code"]) == (404, 4001)
    assert knowledge_client.get(DOCUMENT_URL, headers=owner_headers).status_code == 200


# 做什么：确认清选库只影响当前用户的有效会话，并保留会话更新时间
# 入参：预建库、隔离客户端、数据库会话工厂和归属请求头
# 返回：无；影响范围、计数或更新时间不符时断言失败
def test_delete_base_clears_only_owned_active_sessions(knowledge_client, knowledge_sessions, owner_headers, document_bases):
    original_time = datetime(2026, 1, 1)
    with knowledge_sessions.begin() as seed_session:
        seed_session.add_all([
            ChatSession(id=1, user_id=OWNER_ID, knowledge_base_id=OWNER_BASE_ID, updated_at=original_time),
            ChatSession(id=2, user_id=OWNER_ID, knowledge_base_id=OWNER_BASE_ID, updated_at=original_time),
            ChatSession(id=3, user_id=OTHER_OWNER_ID, knowledge_base_id=OWNER_BASE_ID),
            ChatSession(id=4, user_id=OWNER_ID, knowledge_base_id=OWNER_BASE_ID, is_deleted=DELETED),
            ChatSession(id=5, user_id=OWNER_ID, knowledge_base_id=SECOND_OWNER_BASE_ID),
        ])
    response = knowledge_client.delete(BASE_URL, headers=owner_headers)
    assert response.status_code == 200 and response.json()["data"]["cleared_sessions"] == 2
    with knowledge_sessions() as query_session:
        sessions = query_session.scalars(select(ChatSession).order_by(ChatSession.id)).all()
        assert [session.knowledge_base_id for session in sessions] == [
            None, None, OWNER_BASE_ID, OWNER_BASE_ID, SECOND_OWNER_BASE_ID,
        ]
        assert [session.updated_at for session in sessions[:2]] == [original_time, original_time]


# 做什么：确认只删除指定文档的片段，其余文档仍能列表、查看与检索
# 入参：预建库、隔离客户端、归属请求头和 SQLite 搜索替身
# 返回：无；同库另一篇文档受影响或删除计数不符时断言失败
def test_delete_document_keeps_its_neighbor(knowledge_client, owner_headers, document_bases, monkeypatch):
    removed_id = upload_single_chunk(knowledge_client, owner_headers)
    remaining_id = upload_single_chunk(knowledge_client, owner_headers)
    monkeypatch.setattr(retrieval, "search_chunks", search_in_sqlite)
    response = knowledge_client.delete(f"{DOCUMENT_URL}/{removed_id}", headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["data"] == {"id": removed_id, "deleted_chunks": 1}
    listed = knowledge_client.get(DOCUMENT_URL, headers=owner_headers).json()["data"]
    assert listed["total"] == 1 and listed["items"][0]["id"] == remaining_id
    hidden = knowledge_client.get(f"{DOCUMENT_URL}/{removed_id}/chunks", headers=owner_headers)
    assert (hidden.status_code, hidden.json()["code"]) == (404, 4002)
    remaining = knowledge_client.get(f"{DOCUMENT_URL}/{remaining_id}/chunks", headers=owner_headers)
    assert remaining.status_code == 200 and remaining.json()["data"]["items"][0]["content"] == PROBE_CONTENT
    found = knowledge_client.post(f"{BASE_URL}/search", json={"query": SEARCH_WORD}, headers=owner_headers)
    assert [item["document_id"] for item in found.json()["data"]["items"]] == [remaining_id]


# 做什么：验证缺失、已删和别库文档都使用文档错误码
# 入参：预建库、隔离客户端、归属请求头和文档初始状态
# 返回：无；未返回 404/4002 时断言失败
@pytest.mark.parametrize("document_state", ["missing", "deleted", "other_base"])
def test_delete_document_hides_inaccessible_document(knowledge_client, owner_headers, document_bases, document_state):
    document_id = 999
    if document_state == "deleted":
        document_id = upload_single_chunk(knowledge_client, owner_headers)
        assert knowledge_client.delete(f"{DOCUMENT_URL}/{document_id}", headers=owner_headers).status_code == 200
    if document_state == "other_base":
        document_id = upload_single_chunk(knowledge_client, owner_headers, SECOND_OWNER_BASE_ID)
    response = knowledge_client.delete(f"{DOCUMENT_URL}/{document_id}", headers=owner_headers)
    assert (response.status_code, response.json()["code"]) == (404, 4002)


# 做什么：确认原文页的偏移、已配置的重叠及空页口径
# 入参：预建库、已上传的多片段文档、客户端和请求头
# 返回：无；偏移有间隙或响应字段错误时断言失败
def test_list_chunks_pages_in_original_order(knowledge_client, owner_headers, chunked_upload):
    chunks_url = f"{DOCUMENT_URL}/{chunked_upload['id']}/chunks"
    complete = knowledge_client.get(chunks_url, headers=owner_headers).json()["data"]
    items = complete["items"]
    assert complete["total"] == len(items) > 1
    assert items[0]["char_start"] == 0 and items[-1]["char_end"] == chunked_upload["char_count"]
    assert set(items[0]) == {"id", "chunk_index", "heading_path", "char_start", "char_end", "content"}
    for previous, current in zip(items, items[1:]):
        assert current["chunk_index"] == previous["chunk_index"] + 1
        assert CHUNK_OVERLAP_MIN <= previous["char_end"] - current["char_start"] <= CHUNK_OVERLAP_MAX
    second = knowledge_client.get(f"{chunks_url}?page=2&page_size=1", headers=owner_headers)
    assert second.json()["data"] == {"items": [items[1]], "total": len(items), "page": 2, "page_size": 1}
    empty = knowledge_client.get(f"{chunks_url}?page=999&page_size=1", headers=owner_headers)
    assert empty.json()["data"] == {"items": [], "total": len(items), "page": 999, "page_size": 1}
    oversized = knowledge_client.get(f"{chunks_url}?page_size=51", headers=owner_headers)
    assert (oversized.status_code, oversized.json()["code"]) == (422, 1000)


# 做什么：确认库和文档的不可见错误各自对应正确的错误码
# 入参：预建库、两个账号、文档路由及目标库编号
# 返回：无；源账号能够访问他人文档时断言失败
@pytest.mark.parametrize(("base_id", "expected_code"), [(OTHER_BASE_ID, 4001), (OWNER_BASE_ID, 4002)])
def test_document_endpoints_hide_other_owner(knowledge_client, owner_headers, document_bases, base_id, expected_code):
    other_headers = {"Authorization": f"Bearer {create_access_token(OTHER_OWNER_ID)}"}
    document_id = upload_single_chunk(knowledge_client, other_headers, OTHER_BASE_ID)
    document_url = f"{KNOWLEDGE_BASE_URL}/{base_id}/documents/{document_id}"
    listed = knowledge_client.get(f"{document_url}/chunks", headers=owner_headers)
    deleted = knowledge_client.delete(document_url, headers=owner_headers)
    assert (listed.status_code, listed.json()["code"]) == (404, expected_code)
    assert (deleted.status_code, deleted.json()["code"]) == (404, expected_code)


# 做什么：模拟 MySQL 搜索并记录等待及重建的调用顺序
# 入参：临时替换工具、日志捕获器、各次搜索结果和预期调用
# 返回：无；调用顺序或日志含义不符时断言失败
@pytest.mark.parametrize(("matches", "expected_actions", "log_message"), [
    ([True], ["search"], "命中条数=1"),
    ([False, True], ["search", "sleep", "search"], "命中条数=1"),
    ([False, False, True], ["search", "sleep", "search", "rebuild", "search"], "命中条数=1"),
    ([False, False, False], ["search", "sleep", "search", "rebuild", "search"], "需人工重建"),
])
def test_upload_index_probe_order(monkeypatch, caplog, matches, expected_actions, log_message):
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "mysql"
    chunk = Mock(content=f"  {PROBE_CONTENT}")
    actions = Mock()
    actions.search.side_effect = [[Mock()] if matched else [] for matched in matches]
    monkeypatch.setattr(retrieval, "search_chunks", actions.search)
    monkeypatch.setattr(document_store, "sleep", actions.sleep)
    monkeypatch.setattr(document_store, "rebuild_fulltext_index", actions.rebuild)
    with caplog.at_level("INFO", logger=document_store.__name__):
        document_store.verify_search_index_after_upload(session, OWNER_ID, OWNER_BASE_ID, [chunk])
    expected_calls = {
        "search": call.search(session, OWNER_ID, OWNER_BASE_ID, PROBE_CONTENT[:12], 1),
        "sleep": call.sleep(0.2), "rebuild": call.rebuild(session),
    }
    assert actions.mock_calls == [expected_calls[action] for action in expected_actions]
    assert log_message in caplog.text


# 做什么：确认 SQLite 上传就绪且完全跳过索引自检
# 入参：预建库、隔离客户端、归属请求头和查询替身
# 返回：无；搜索、重建或 SQL OPTIMIZE 被调用时断言失败
def test_upload_skips_index_check_in_sqlite(knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch):
    search = Mock()
    rebuild = Mock()
    statements = Mock()
    monkeypatch.setattr(retrieval, "search_chunks", search)
    monkeypatch.setattr(document_store, "rebuild_fulltext_index", rebuild)
    event.listen(knowledge_sessions.kw["bind"], "before_cursor_execute", statements)
    response = knowledge_client.post(DOCUMENT_URL, json={"title": "索引验收", "content": PROBE_CONTENT}, headers=owner_headers)
    assert response.status_code == 200 and response.json()["data"]["status"] == DocumentStatus.READY
    search.assert_not_called()
    rebuild.assert_not_called()
    assert all("OPTIMIZE" not in statement.args[2] for statement in statements.call_args_list)


# 做什么：确认上传自检看得见已提交的就绪文档，异常只记告警
# 入参：隔离会话工厂、预建库、替身工具和日志捕获器
# 返回：无；事务时序、返回状态或日志不符时断言失败
def test_upload_probe_failure_keeps_document_ready(knowledge_sessions, document_bases, monkeypatch, caplog):
    original_probe = document_store.verify_search_index_after_upload
    bind = Mock()
    bind.dialect.name = "mysql"
    broken_search = Mock(side_effect=RuntimeError("测试探针查询失败"))
    monkeypatch.setattr(retrieval, "search_chunks", broken_search)

    # 做什么：确认另一条连接能看到刚提交的文档，再触发自检错误
    # 入参：原上传会话、用户编号、库编号与新切片
    # 返回：无；写入仍在事务中或文档不可见时断言失败
    def verify_committed_upload(db_session, owner_id, knowledge_base_id, chunks):
        assert not db_session.in_transaction()
        with knowledge_sessions() as query_session:
            assert query_session.scalar(select(KnowledgeDocument.status)) == DocumentStatus.READY
        monkeypatch.setattr(db_session, "get_bind", Mock(return_value=bind))
        original_probe(db_session, owner_id, knowledge_base_id, chunks)

    monkeypatch.setattr(document_store, "verify_search_index_after_upload", verify_committed_upload)
    with knowledge_sessions() as db_session:
        payload = KnowledgeDocumentCreate(title="索引异常自检", content=PROBE_CONTENT)
        document = knowledge.create_knowledge_document(db_session, OWNER_ID, OWNER_BASE_ID, payload)
        assert document.status == DocumentStatus.READY and not db_session.in_transaction()
    broken_search.assert_called_once()
    assert "上传索引自检异常" in caplog.text and "测试探针查询失败" not in caplog.text


# 做什么：确认两类删除在数据提交之后各触发一次索引自检，且不再无条件重建
# 入参：隔离客户端、数据库会话工厂、请求头、预建库和删除对象
# 返回：无；事务时序、状态、自检次数或重建次数不符时断言失败
@pytest.mark.parametrize("delete_base", [True, False])
def test_delete_index_check_runs_after_commit(knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch, delete_base):
    document_id = upload_single_chunk(knowledge_client, owner_headers)

    # 做什么：用另一条连接读取已提交的软删除标志
    # 入参：删除后索引自检的独立写会话
    # 返回：无；提交时序或软删除状态不符时断言失败
    def check_committed_delete(db_session, owner_id, knowledge_base_id):
        assert not db_session.in_transaction()
        with knowledge_sessions() as query_session:
            assert query_session.get(KnowledgeDocument, document_id).is_deleted == DELETED
            assert query_session.scalar(select(KnowledgeChunk.is_deleted)) == DELETED

    index_check = Mock(side_effect=check_committed_delete)
    rebuild = Mock()
    monkeypatch.setattr(document_store, "verify_search_index_after_delete", index_check)
    monkeypatch.setattr(document_store, "rebuild_fulltext_index", rebuild)
    delete_url = BASE_URL if delete_base else f"{DOCUMENT_URL}/{document_id}"
    response = knowledge_client.delete(delete_url, headers=owner_headers)
    assert response.status_code == 200 and response.json()["code"] == 0
    index_check.assert_called_once()
    rebuild.assert_not_called()


# 做什么：确认非 MySQL 环境删除时不做整表重建，删除本身照常成功
# 入参：隔离客户端、数据库会话工厂、请求头和预建文档
# 返回：重建被调用或删除抛错时断言失败
def test_delete_skips_rebuild_without_mysql(knowledge_client, knowledge_sessions, owner_headers, document_bases, monkeypatch):
    document_id = upload_single_chunk(knowledge_client, owner_headers)

    # 做什么：把重建替换成会失败的替身，证明删除路径不再依赖它
    # 入参：会话
    # 返回：无；被调用时抛出断言错误
    def refuse_rebuild(_db_session):
        raise AssertionError("非 MySQL 环境不应重建全文索引")

    monkeypatch.setattr(document_store, "rebuild_fulltext_index", refuse_rebuild)
    with knowledge_sessions() as db_session:
        result = knowledge.delete_knowledge_document(db_session, OWNER_ID, OWNER_BASE_ID, document_id)
        assert result.deleted_chunks >= 1 and not db_session.in_transaction()


# 做什么：核对索引维护通过自动提交连接执行并识别 MySQL 返回状态
# 入参：MySQL 返回行的替身和预期是否成功
# 返回：无；SQL、连接选项或返回值不符时断言失败
@pytest.mark.parametrize(("message_type", "message", "expected"), [("status", "OK", True), ("error", "Denied", False)])
def test_rebuild_detects_mysql_status(message_type, message, expected):
    session = MagicMock()
    bind = session.get_bind.return_value
    bind.dialect.name = "mysql"
    options = bind.engine.connect.return_value.execution_options
    connection = options.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"Msg_type": message_type, "Msg_text": message},
    ]
    assert index_maintenance.rebuild_fulltext_index(session) is expected
    options.assert_called_once_with(isolation_level="AUTOCOMMIT")
    assert str(connection.execute.call_args.args[0]) == "OPTIMIZE TABLE knowledge_chunks"
