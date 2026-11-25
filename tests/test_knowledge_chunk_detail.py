"""验证按全局切片编号查看本人引用原文。"""
import pytest

from app.core.security import create_access_token
from app.models.knowledge import KnowledgeDocument
from tests.test_knowledge import (
    DELETED, DOCUMENT_URL, OTHER_OWNER_ID, OWNER_BASE_ID,
    document_bases, knowledge_client, knowledge_sessions, owner_headers,
)

CHUNKS_PATH = "/knowledge-chunks"
UNKNOWN_CHUNK_ID = 999
TITLE = "对话引用说明"
DOCUMENT_CONTENT = "第一节介绍引用。" * 80 + "\n\n  第二节保留换行🙂。\n这一行也属于第二节。  "


# 做什么：通过现有接口上传文档并取得非零偏移的切片
# 入参：隔离客户端、本人凭证和预建知识库
# 返回：文档编号和真实切片摘要；上传或分页失败时断言失败
@pytest.fixture
def uploaded_chunk(knowledge_client, owner_headers, document_bases):
    upload = knowledge_client.post(
        DOCUMENT_URL, json={"title": TITLE, "content": DOCUMENT_CONTENT}, headers=owner_headers,
    )
    assert upload.status_code == 200
    document_id = upload.json()["data"]["id"]
    listed = knowledge_client.get(f"{DOCUMENT_URL}/{document_id}/chunks", headers=owner_headers)
    assert listed.status_code == 200
    chunks = listed.json()["data"]["items"]
    assert len(chunks) == 2 and chunks[1]["char_start"] > 0
    return document_id, chunks[1]


# 做什么：验证详情字段及正文按已保存原文的字符区间精确对齐
# 入参：隔离客户端、数据库会话工厂、本人凭证及上传结果
# 返回：无；任何字段、区间或正文不同均断言失败
def test_chunk_detail_matches_uploaded_source(knowledge_client, knowledge_sessions, owner_headers, uploaded_chunk):
    document_id, chunk = uploaded_chunk
    response = knowledge_client.get(f"{CHUNKS_PATH}/{chunk['id']}", headers=owner_headers)
    assert response.status_code == 200 and response.json()["code"] == 0
    detail = response.json()["data"]
    with knowledge_sessions() as session:
        document = session.get(KnowledgeDocument, document_id)
        assert document.raw_content == DOCUMENT_CONTENT
        source_slice = document.raw_content[chunk["char_start"]:chunk["char_end"]]
    assert detail == {
        "chunk_id": chunk["id"], "knowledge_base_id": OWNER_BASE_ID, "document_id": document_id,
        "document_title": TITLE, "chunk_index": chunk["chunk_index"], "heading_path": chunk["heading_path"],
        "char_start": chunk["char_start"], "char_end": chunk["char_end"], "content": source_slice,
    }
    assert detail["content"] == chunk["content"]


# 做什么：确认另一登录用户不能按编号读取该切片
# 入参：隔离客户端和本人上传的切片
# 返回：无；错误状态或错误码不同均断言失败
def test_chunk_detail_hides_other_users_chunk(knowledge_client, uploaded_chunk):
    _, chunk = uploaded_chunk
    other_headers = {"Authorization": f"Bearer {create_access_token(OTHER_OWNER_ID)}"}
    response = knowledge_client.get(f"{CHUNKS_PATH}/{chunk['id']}", headers=other_headers)
    assert response.status_code == 404 and response.json()["code"] == 4003


# 做什么：确认不存在的切片返回统一错误
# 入参：隔离客户端和本人凭证
# 返回：无；错误状态、错误码或文案不同均断言失败
def test_chunk_detail_rejects_missing_id(knowledge_client, owner_headers):
    response = knowledge_client.get(f"{CHUNKS_PATH}/{UNKNOWN_CHUNK_ID}", headers=owner_headers)
    assert response.status_code == 404
    assert (response.json()["code"], response.json()["message"]) == (4003, "知识库切片不存在")


# 做什么：删除文档后确认旧引用不可查看
# 入参：隔离客户端、本人凭证和上传结果
# 返回：无；删除失败或仍可读取切片时断言失败
def test_chunk_detail_rejects_deleted_document_chunk(knowledge_client, owner_headers, uploaded_chunk):
    document_id, chunk = uploaded_chunk
    removed = knowledge_client.delete(f"{DOCUMENT_URL}/{document_id}", headers=owner_headers)
    assert removed.status_code == 200
    response = knowledge_client.get(f"{CHUNKS_PATH}/{chunk['id']}", headers=owner_headers)
    assert response.status_code == 404 and response.json()["code"] == 4003


# 做什么：确认文档检查能阻止仍存有切片的旧数据泄漏
# 入参：隔离数据库、客户端、本人凭证和上传结果
# 返回：无；异常文档的仍有效切片可见时断言失败
@pytest.mark.parametrize("invalidate_document", ["deleted", "different_owner"])
def test_chunk_detail_rechecks_document(knowledge_client, knowledge_sessions, owner_headers, uploaded_chunk, invalidate_document):
    document_id, chunk = uploaded_chunk
    with knowledge_sessions.begin() as session:
        document = session.get(KnowledgeDocument, document_id)
        if invalidate_document == "deleted":
            document.is_deleted = DELETED
        else:
            document.owner_id = OTHER_OWNER_ID
    response = knowledge_client.get(f"{CHUNKS_PATH}/{chunk['id']}", headers=owner_headers)
    assert response.status_code == 404 and response.json()["code"] == 4003


# 做什么：确认访问详情必须登录
# 入参：隔离客户端，不携带请求凭证
# 返回：无；返回非 401/1002 时断言失败
def test_chunk_detail_requires_login(knowledge_client):
    response = knowledge_client.get(f"{CHUNKS_PATH}/{UNKNOWN_CHUNK_ID}")
    assert response.status_code == 401 and response.json()["code"] == 1002
