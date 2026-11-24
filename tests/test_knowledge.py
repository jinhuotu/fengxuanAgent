"""使用独立 SQLite 库验证个人知识库与文档接口。"""
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.knowledge import DocumentStatus
from app.core.security import create_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.knowledge import KnowledgeBase, KnowledgeDocument
from app.models.user import User

KNOWLEDGE_BASE_URL = "/knowledge-bases"
OWNER_ID = 1
OTHER_OWNER_ID = 2
DELETED = 1
OWNER_BASE_ID = 1
OTHER_BASE_ID = 2
DELETED_BASE_ID = 3
SECOND_OWNER_BASE_ID = 4
MISSING_BASE_ID = 999
DOCUMENT_URL = f"{KNOWLEDGE_BASE_URL}/{OWNER_BASE_ID}/documents"


# 做什么：为每条测试准备独立库、无角色用户及已提交的账号记录
# 入参：pytest 临时目录与用于替换会话工厂的 monkeypatch
# 返回：测试会话工厂；结束或异常时释放数据库连接
@pytest.fixture
def knowledge_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker]:
    database_url = URL.create("sqlite", database=str(tmp_path / "knowledge.sqlite3"))
    test_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(test_engine)
        test_sessions = sessionmaker(bind=test_engine)
        password_hash = hash_password("isolated-knowledge-password")
        with test_sessions.begin() as seed_session:
            seed_session.add_all([
                User(id=OWNER_ID, username="knowledge_owner", password_hash=password_hash),
                User(id=OTHER_OWNER_ID, username="other_owner", password_hash=password_hash),
            ])
        monkeypatch.setattr(database_session, "SessionLocal", test_sessions)
        yield test_sessions
    finally:
        test_engine.dispose()


# 做什么：通过真实路由和鉴权依赖向隔离数据库发请求
# 入参：已替换会话工厂的隔离数据库夹具
# 返回：测试客户端；结束或异常时关闭客户端
@pytest.fixture
def knowledge_client(knowledge_sessions: sessionmaker) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


# 做什么：为已存在的知识库归属用户签发隔离测试凭证
# 入参：无，使用测试库固定的归属用户编号
# 返回：携带有效 Bearer 凭证的请求头；签发异常直接抛出
@pytest.fixture
def owner_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(OWNER_ID)}"}


# 做什么：检查去空白后的边界长度、同名建库、可空说明及数据库归属
# 入参：隔离客户端、数据库会话工厂和本人请求头
# 返回：无；响应字段或已提交数据不符合预期时断言失败
def test_create_knowledge_base(knowledge_client, knowledge_sessions, owner_headers) -> None:
    name = "资料" * 25
    payload = {"name": f"  {name}\t", "description": " 个人资料 "}
    create_response = knowledge_client.post(KNOWLEDGE_BASE_URL, json=payload, headers=owner_headers)
    assert create_response.status_code == 200
    create_result = create_response.json()
    assert create_result["code"] == 0
    created_item = create_result["data"]
    assert set(created_item) == {"id", "name", "description", "document_count", "created_at"}
    assert created_item["name"] == name
    assert created_item["description"] == payload["description"]
    assert created_item["document_count"] == 0
    assert datetime.fromisoformat(created_item["created_at"])
    duplicate_response = knowledge_client.post(
        KNOWLEDGE_BASE_URL, json={"name": name}, headers=owner_headers
    )
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["data"]["description"] is None
    assert duplicate_response.json()["data"]["id"] != created_item["id"]
    with knowledge_sessions() as query_session:
        owner_query = select(KnowledgeBase.owner_id).order_by(KnowledgeBase.id)
        assert query_session.scalars(owner_query).all() == [OWNER_ID, OWNER_ID]


# 做什么：检查分页结果只含未删除的本人库，并按编号倒序
# 入参：隔离客户端、数据库会话工厂和本人请求头
# 返回：无；归属、排序、默认值或空页不符时断言失败
def test_list_only_owners_active_knowledge_bases(knowledge_client, knowledge_sessions, owner_headers) -> None:
    libraries = [
        KnowledgeBase(id=1, owner_id=OWNER_ID, name="旧库"),
        KnowledgeBase(id=2, owner_id=OTHER_OWNER_ID, name="他人库"),
        KnowledgeBase(id=3, owner_id=OWNER_ID, name="已删除", is_deleted=DELETED),
        KnowledgeBase(id=4, owner_id=OWNER_ID, name="新库"),
    ]
    with knowledge_sessions.begin() as seed_session:
        seed_session.add_all(libraries)
    list_response = knowledge_client.get(KNOWLEDGE_BASE_URL, headers=owner_headers)
    assert list_response.status_code == 200
    assert list_response.json()["code"] == 0
    page_result = list_response.json()["data"]
    items = page_result["items"]
    assert [(item["id"], item["document_count"]) for item in items] == [(4, 0), (1, 0)]
    assert all("owner_id" not in item and "is_deleted" not in item for item in items)
    assert (page_result["total"], page_result["page"], page_result["page_size"]) == (2, 1, 20)
    second_response = knowledge_client.get(f"{KNOWLEDGE_BASE_URL}?page=2&page_size=1", headers=owner_headers)
    assert second_response.status_code == 200
    assert second_response.json()["data"] == {"items": [items[1]], "total": 2, "page": 2, "page_size": 1}
    empty_response = knowledge_client.get(f"{KNOWLEDGE_BASE_URL}?page=3&page_size=1", headers=owner_headers)
    assert empty_response.status_code == 200
    assert empty_response.json()["data"] == {"items": [], "total": 2, "page": 3, "page_size": 1}


# 做什么：确认非法库名和超长说明走统一字段校验错误
# 入参：隔离客户端、本人请求头和非法请求体
# 返回：无；未返回 422/1000 时断言失败
@pytest.mark.parametrize("payload", [
    {"name": ""},
    {"name": " \t "},
    {"name": "库" * 51},
    {"name": "资料", "description": "字" * 201},
])
def test_create_rejects_invalid_fields(knowledge_client, owner_headers, payload) -> None:
    response = knowledge_client.post(KNOWLEDGE_BASE_URL, json=payload, headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["code"] == 1000


# 做什么：确认页码和每页条数的边界由框架校验
# 入参：隔离客户端、本人请求头和非法分页参数
# 返回：无；未返回 422/1000 时断言失败
@pytest.mark.parametrize("query_string", ["page=0", "page_size=0", "page_size=51"])
def test_list_rejects_invalid_pagination(knowledge_client, owner_headers, query_string) -> None:
    response = knowledge_client.get(f"{KNOWLEDGE_BASE_URL}?{query_string}", headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["code"] == 1000


# 做什么：确认两个接口都要求有效登录凭证
# 入参：隔离客户端与没有授权头的请求方法及请求体
# 返回：无；未返回 401/1002 时断言失败
@pytest.mark.parametrize(("method", "payload"), [("POST", {"name": "个人库"}), ("GET", None)])
def test_knowledge_requires_login(knowledge_client, method, payload) -> None:
    response = knowledge_client.request(method, KNOWLEDGE_BASE_URL, json=payload)
    assert response.status_code == 401
    assert response.json()["code"] == 1002


# 做什么：建立本人、他人、已删除和第二个本人知识库
# 入参：独立数据库会话工厂
# 返回：无；种子写入失败时回滚并抛出异常
@pytest.fixture
def document_bases(knowledge_sessions: sessionmaker) -> None:
    knowledge_bases = [
        KnowledgeBase(id=OWNER_BASE_ID, owner_id=OWNER_ID, name="本人库"),
        KnowledgeBase(id=OTHER_BASE_ID, owner_id=OTHER_OWNER_ID, name="他人库"),
        KnowledgeBase(id=DELETED_BASE_ID, owner_id=OWNER_ID, name="已删除库", is_deleted=DELETED),
        KnowledgeBase(id=SECOND_OWNER_BASE_ID, owner_id=OWNER_ID, name="第二个本人库"),
    ]
    with knowledge_sessions.begin() as seed_session:
        seed_session.add_all(knowledge_bases)


# 做什么：检查标题去空白、正文原样保存、字符数和初始状态
# 入参：隔离客户端、本人账号请求头、测试库以及预建的知识库
# 返回：无；响应或持久化内容与预期不符时断言失败
def test_upload_document_preserves_content(knowledge_client, owner_headers, knowledge_sessions, document_bases) -> None:
    title = "文" * 100
    content = " 中\n😀 "
    payload = {"title": f" {title}\t", "content": content}
    response = knowledge_client.post(DOCUMENT_URL, json=payload, headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["code"] == 0
    document_item = response.json()["data"]
    assert set(document_item) == {"id", "title", "char_count", "status", "created_at"}
    assert document_item["title"] == title
    assert document_item["char_count"] == len(content)
    assert type(document_item["status"]) is int and document_item["status"] == DocumentStatus.PENDING
    assert datetime.fromisoformat(document_item["created_at"])
    with knowledge_sessions() as query_session:
        document = query_session.get(KnowledgeDocument, document_item["id"])
        assert document is not None
        assert (document.knowledge_base_id, document.owner_id) == (OWNER_BASE_ID, OWNER_ID)
        assert document.raw_content == content
        assert (document.char_count, document.status) == (len(content), DocumentStatus.PENDING)


# 做什么：确认两条接口对他人库、已删除库和不存在的库统一隐藏
# 入参：隔离客户端、本人请求头、种子库、请求方法和知识库编号
# 返回：无；未统一返回 404/4001 或意外落库时断言失败
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("knowledge_base_id", [OTHER_BASE_ID, DELETED_BASE_ID, MISSING_BASE_ID])
def test_document_endpoints_hide_inaccessible_bases(
    knowledge_client, owner_headers, knowledge_sessions, document_bases, method, knowledge_base_id
) -> None:
    url = f"{KNOWLEDGE_BASE_URL}/{knowledge_base_id}/documents"
    payload = {"title": "资料", "content": "正文"} if method == "POST" else None
    response = knowledge_client.request(method, url, json=payload, headers=owner_headers)
    assert response.status_code == 404
    assert response.json() == {"code": 4001, "message": "知识库不存在", "data": None}
    with knowledge_sessions() as query_session:
        assert query_session.scalar(select(KnowledgeDocument.id)) is None


# 做什么：确认文档列表只返回本库未删除的本人文档，按编号倒序分页
# 入参：隔离客户端、本人请求头、测试库和预建知识库
# 返回：无；范围、公开字段、顺序或分页不符时断言失败
def test_document_list_filters_by_base_owner_and_deletion(
    knowledge_client, owner_headers, knowledge_sessions, document_bases
) -> None:
    documents = [
        KnowledgeDocument(id=1, knowledge_base_id=OWNER_BASE_ID, owner_id=OWNER_ID, title="旧文档",
                          raw_content="原文", char_count=2, status=DocumentStatus.PENDING),
        KnowledgeDocument(id=2, knowledge_base_id=SECOND_OWNER_BASE_ID, owner_id=OWNER_ID, title="其他库",
                          raw_content="原文", char_count=2, status=DocumentStatus.PENDING),
        KnowledgeDocument(id=3, knowledge_base_id=OWNER_BASE_ID, owner_id=OWNER_ID, title="已删除",
                          raw_content="原文", char_count=2, status=DocumentStatus.PENDING, is_deleted=DELETED),
        KnowledgeDocument(id=4, knowledge_base_id=OWNER_BASE_ID, owner_id=OTHER_OWNER_ID, title="他人文档",
                          raw_content="原文", char_count=2, status=DocumentStatus.PENDING),
        KnowledgeDocument(id=5, knowledge_base_id=OWNER_BASE_ID, owner_id=OWNER_ID, title="新文档",
                          raw_content="原文", char_count=2, status=DocumentStatus.FAILED),
    ]
    with knowledge_sessions.begin() as seed_session:
        seed_session.add_all(documents)
    response = knowledge_client.get(DOCUMENT_URL, headers=owner_headers)
    assert response.status_code == 200 and response.json()["code"] == 0
    page_result = response.json()["data"]
    items = page_result["items"]
    assert (page_result["total"], page_result["page"], page_result["page_size"]) == (2, 1, 20)
    assert [(item["id"], item["status"]) for item in items] == [(5, 6), (1, 1)]
    assert all(set(item) == {"id", "title", "char_count", "status", "created_at"} for item in items)
    second_page = knowledge_client.get(f"{DOCUMENT_URL}?page=2&page_size=1", headers=owner_headers)
    assert second_page.status_code == 200
    assert second_page.json()["data"] == {"items": [items[1]], "total": 2, "page": 2, "page_size": 1}


# 做什么：确认纯文本接口拒绝空正文、超长正文和非法标题
# 入参：隔离客户端、本人请求头、预建知识库和非法请求体
# 返回：无；未返回 422/1000 时断言失败
@pytest.mark.parametrize("payload", [
    {"title": "资料", "content": "字" * 50001},
    {"title": "资料", "content": ""},
    {"title": " \t ", "content": "正文"},
    {"title": "字" * 101, "content": "正文"},
])
def test_upload_rejects_invalid_document_fields(knowledge_client, owner_headers, document_bases, payload) -> None:
    response = knowledge_client.post(DOCUMENT_URL, json=payload, headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["code"] == 1000


# 做什么：确认文档列表每页最多五十条
# 入参：隔离客户端、本人请求头与预建知识库
# 返回：无；未返回 422/1000 时断言失败
def test_document_list_rejects_oversized_page(knowledge_client, owner_headers, document_bases) -> None:
    response = knowledge_client.get(f"{DOCUMENT_URL}?page_size=51", headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["code"] == 1000


# 做什么：确认两条文档接口都复用登录依赖
# 入参：隔离客户端、预建知识库和不带凭证的请求方法
# 返回：无；未返回 401/1002 时断言失败
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_document_endpoints_require_login(knowledge_client, document_bases, method) -> None:
    payload = {"title": "资料", "content": "正文"} if method == "POST" else None
    response = knowledge_client.request(method, DOCUMENT_URL, json=payload)
    assert response.status_code == 401
    assert response.json()["code"] == 1002


# 做什么：确认上传两篇后库列表实时计数，并过滤软删除和他人文档
# 入参：隔离客户端、本人请求头、测试库和预建知识库
# 返回：无；计数、已失败文档口径或空库缺省值不符时断言失败
def test_base_list_counts_uploaded_documents(
    knowledge_client, owner_headers, knowledge_sessions, document_bases
) -> None:
    first_upload = knowledge_client.post(DOCUMENT_URL, json={"title": "第一篇", "content": " "}, headers=owner_headers)
    second_upload = knowledge_client.post(DOCUMENT_URL, json={"title": "第二篇", "content": "正文"}, headers=owner_headers)
    assert first_upload.status_code == second_upload.status_code == 200
    initial_page = knowledge_client.get(KNOWLEDGE_BASE_URL, headers=owner_headers)
    assert initial_page.status_code == 200
    initial_items = initial_page.json()["data"]["items"]
    expected_counts = {SECOND_OWNER_BASE_ID: 0, OWNER_BASE_ID: 2}
    assert {item["id"]: item["document_count"] for item in initial_items} == expected_counts
    with knowledge_sessions.begin() as seed_session:
        failed_document = seed_session.get(KnowledgeDocument, second_upload.json()["data"]["id"])
        assert failed_document is not None
        failed_document.status = DocumentStatus.FAILED
        seed_session.add_all([
            KnowledgeDocument(knowledge_base_id=OWNER_BASE_ID, owner_id=OWNER_ID, title="已删除",
                              raw_content="原文", char_count=2, status=DocumentStatus.FAILED, is_deleted=DELETED),
            KnowledgeDocument(knowledge_base_id=OWNER_BASE_ID, owner_id=OTHER_OWNER_ID, title="他人文档",
                              raw_content="原文", char_count=2, status=DocumentStatus.PENDING),
        ])
    updated_page = knowledge_client.get(KNOWLEDGE_BASE_URL, headers=owner_headers)
    assert updated_page.status_code == 200
    updated_items = updated_page.json()["data"]["items"]
    assert {item["id"]: item["document_count"] for item in updated_items} == expected_counts
