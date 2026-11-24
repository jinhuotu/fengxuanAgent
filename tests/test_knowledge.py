"""使用独立 SQLite 库验证建库、私有分页及接口校验。"""
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import create_access_token, hash_password
from app.db import session as database_session
from app.db.base import Base
from app.main import app
from app.models.knowledge import KnowledgeBase
from app.models.user import User

KNOWLEDGE_BASE_URL = "/knowledge-bases"
OWNER_ID = 1
OTHER_OWNER_ID = 2
DELETED = 1


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
