"""向量索引与双路检索：同步、归属过滤、陈旧点校验和融合排序。"""
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import URL, create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.knowledge import DocumentStatus
from app.db.base import Base
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag import retrieval, vector_index
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services import knowledge as knowledge_service

VECTOR_DIM = 512
OWNER_ID = 1
OTHER_OWNER_ID = 2


# 做什么：用字符分桶生成稳定向量，避免测试依赖真实模型
# 入参：任意文本
# 返回：512 维向量，公共字符越多余弦越接近
def fake_vector(text: str) -> list[float]:
    values = [0.0] * VECTOR_DIM
    for character in text:
        values[ord(character) % VECTOR_DIM] += 1.0
    return values


# 做什么：为每条用例建立独立 SQLite 库
# 入参：临时目录
# 返回：会话工厂；结束时释放连接
@pytest.fixture
def sessions(tmp_path: Path):
    engine = create_engine(URL.create("sqlite", database=str(tmp_path / "vector.sqlite3")))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


# 做什么：准备内存 Qdrant、假向量和启用的配置
# 入参：monkeypatch
# 返回：内存客户端；所有向量调用改走假实现
@pytest.fixture
def memory_qdrant(monkeypatch: pytest.MonkeyPatch):
    client = QdrantClient(":memory:")
    settings = get_settings()
    monkeypatch.setattr(settings, "vector_enabled", True)
    monkeypatch.setattr(settings, "embedding_dim", VECTOR_DIM)
    monkeypatch.setattr(settings, "qdrant_collection", f"test_chunks_{uuid4().hex}")
    monkeypatch.setattr(vector_index, "get_client", lambda: client)
    monkeypatch.setattr(vector_index, "embed_passages", lambda texts: [fake_vector(text) for text in texts])
    monkeypatch.setattr(vector_index, "embed_query", lambda text: fake_vector(text))
    yield client


# 做什么：写入一个库、一篇文档和两条切片
# 入参：会话工厂、归属编号、库编号和两条正文
# 返回：切片编号列表
def seed_chunks(sessions, owner_id: int, base_id: int, contents: tuple[str, str]) -> list[int]:
    with sessions.begin() as db:
        db.add(KnowledgeBase(id=base_id, owner_id=owner_id, name=f"库{base_id}"))
        document = KnowledgeDocument(
            id=base_id, knowledge_base_id=base_id, owner_id=owner_id, title="资料",
            raw_content=contents[0] + contents[1], char_count=len(contents[0] + contents[1]),
            status=DocumentStatus.READY,
        )
        db.add(document)
        chunks = [
            KnowledgeChunk(
                id=base_id * 10 + index, knowledge_base_id=base_id, document_id=base_id, owner_id=owner_id,
                chunk_index=index, heading_path="", content=content, char_start=0, char_end=len(content),
            )
            for index, content in enumerate(contents)
        ]
        db.add_all(chunks)
    return [chunk.id for chunk in chunks]


# 做什么：确认同步写入、重复同步不重复计点
# 入参：隔离会话和内存客户端
# 返回：点数不符时失败
def test_sync_document_is_idempotent(sessions, memory_qdrant):
    seed_chunks(sessions, OWNER_ID, 1, ("学习率是步长控制参数。", "MySQL 使用全文索引。"))
    with sessions() as db:
        assert vector_index.sync_document(db, 1, OWNER_ID, 1) == 2
        assert vector_index.sync_document(db, 1, OWNER_ID, 1) == 2
    assert vector_index.count_points() == 2


# 做什么：确认向量检索只返回本人本库的点
# 入参：隔离会话和内存客户端
# 返回：串库或串用户时失败
def test_vector_search_filters_owner_and_base(sessions, memory_qdrant):
    own_ids = seed_chunks(sessions, OWNER_ID, 1, ("学习率是步长控制参数。", "MySQL 使用全文索引。"))
    seed_chunks(sessions, OTHER_OWNER_ID, 2, ("学习率的其他说法。", "学习率与步长。"))
    with sessions() as db:
        vector_index.sync_document(db, 1, OWNER_ID, 1)
        vector_index.sync_document(db, 2, OTHER_OWNER_ID, 2)
        hits = vector_index.search_vectors(OWNER_ID, 1, fake_vector("学习率"), 10)
    assert [hit.chunk_id for hit in hits] == own_ids


# 做什么：确认删文档会移除该文档全部向量点
# 入参：隔离会话和内存客户端
# 返回：残留点时失败
def test_delete_document_removes_points(sessions, memory_qdrant):
    seed_chunks(sessions, OWNER_ID, 1, ("学习率是步长控制参数。", "MySQL 使用全文索引。"))
    with sessions() as db:
        vector_index.sync_document(db, 1, OWNER_ID, 1)
    vector_index.delete_document(1)
    assert vector_index.count_points() == 0


# 做什么：确认向量模式回库校验，陈旧点和已软删切片不进入结果
# 入参：隔离会话、内存客户端和 monkeypatch
# 返回：返回不可见切片时失败
def test_vector_mode_drops_stale_and_deleted_chunks(sessions, memory_qdrant, monkeypatch: pytest.MonkeyPatch):
    chunk_ids = seed_chunks(sessions, OWNER_ID, 1, ("学习率是步长控制参数。", "MySQL 使用全文索引。"))
    with sessions() as db:
        vector_index.sync_document(db, 1, OWNER_ID, 1)
        db.execute(update(KnowledgeChunk).where(KnowledgeChunk.id == chunk_ids[1]).values(is_deleted=1))
        db.commit()
    # 手工写入一条数据库中不存在的陈旧点
    memory_qdrant.upsert(get_settings().qdrant_collection, points=[
        vector_index.models.PointStruct(id=999, vector=fake_vector("学习率"), payload={
            "owner_id": OWNER_ID, "knowledge_base_id": 1, "document_id": 1, "chunk_index": 0,
        }),
    ])
    monkeypatch.setattr(get_settings(), "vector_retrieval_mode", "vector")
    with sessions() as db:
        result = retrieval.search_relevant_chunks(db, OWNER_ID, 1, "学习率怎么调", 3)
    assert [hit["chunk_id"] for hit in result.hits] == [chunk_ids[0]]


# 做什么：确认混合排序里关键词第一名仍然排在最前
# 入参：无，直接构造两路候选
# 返回：融合结果顺序不符时失败
def test_hybrid_keeps_keyword_top1():
    keyword_rows = [
        {"chunk_id": 1, "content": "关键词第一", "score": 30.0},
        {"chunk_id": 2, "content": "关键词第二", "score": 20.0},
    ]
    vector_rows = [
        {"chunk_id": 2, "content": "向量第一", "score": 0.9},
        {"chunk_id": 1, "content": "关键词第一", "score": 0.7},
    ]
    hits = retrieval._fuse_candidates(keyword_rows, vector_rows, top_k=2, min_ratio=0.5)
    assert [hit["chunk_id"] for hit in hits] == [1, 2]
    assert hits[0]["score"] == pytest.approx(16 / 9)


# 做什么：确认向量模式按相对阈值截断，弱候选被丢弃
# 入参：无，直接构造候选
# 返回：保留条数不符时失败
def test_vector_filter_uses_relative_floor():
    candidates = [{"chunk_id": 1, "score": 0.8}, {"chunk_id": 2, "score": 0.45}, {"chunk_id": 3, "score": 0.2}]
    assert [hit["chunk_id"] for hit in retrieval._filter_vector(candidates, 5, 0.5)] == [1, 2]

# 做什么：确认上传后的向量同步不会把请求会话留在未结束的事务里
# 入参：隔离会话和内存客户端
# 返回：事务残留时失败；残留会让随后的全文索引自检无法开启新事务
def test_upload_leaves_no_open_transaction(sessions, memory_qdrant):
    with sessions.begin() as db:
        db.add(KnowledgeBase(id=1, owner_id=OWNER_ID, name="库1"))
    db = sessions()
    try:
        content = "学习率是控制每次参数更新步长的超参数，取值过大会震荡，过小则收敛缓慢。\n\n" * 10
        knowledge_service.create_knowledge_document(db, OWNER_ID, 1, KnowledgeDocumentCreate(title="资料", content=content))
        assert not db.in_transaction()
    finally:
        db.close()
    assert vector_index.count_points() > 0
