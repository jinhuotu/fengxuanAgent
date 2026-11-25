"""Qdrant 向量索引：切片向量化、同步和按归属过滤的相似度检索。"""
from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.knowledge import IS_NOT_DELETED, KnowledgeChunk

logger = get_logger(__name__)
EMBEDDER_LOCK = threading.Lock()
EMBEDDER = None
CLIENT: QdrantClient | None = None
CLIENT_LOCK = threading.Lock()
UPSERT_BATCH = 64


@dataclass(frozen=True)
class VectorHit:
    chunk_id: int
    score: float


# 做什么：判断向量索引是否启用，关闭时不加载模型也不连 Qdrant
# 入参：无
# 返回：启用为 True
def is_enabled() -> bool:
    return get_settings().vector_enabled


# 做什么：创建并复用 Qdrant 客户端
# 入参：无，读配置中的地址和密钥
# 返回：进程内共享客户端；连接参数错误时抛出原始异常
def get_client() -> QdrantClient:
    global CLIENT
    if CLIENT is None:
        settings = get_settings()
        with CLIENT_LOCK:
            if CLIENT is None:
                CLIENT = QdrantClient(
                    url=settings.qdrant_url,
                    api_key=settings.qdrant_api_key.get_secret_value() or None,
                    timeout=settings.qdrant_timeout_seconds,
                )
    return CLIENT


# 做什么：按模型名加载本地 ONNX 向量模型并常驻内存
# 入参：无，读配置中的模型名、缓存目录和下载镜像
# 返回：可复用的 TextEmbedding；首次调用会下载模型
def get_embedder():
    global EMBEDDER
    if EMBEDDER is None:
        settings = get_settings()
        os.environ.setdefault("HF_ENDPOINT", settings.embedding_hf_endpoint)
        # 延迟导入：未启用向量检索时不为 fastembed 付导入和模型加载成本。
        from fastembed import TextEmbedding

        os.makedirs(settings.embedding_cache_dir, exist_ok=True)
        with EMBEDDER_LOCK:
            if EMBEDDER is None:
                EMBEDDER = TextEmbedding(settings.embedding_model, cache_dir=settings.embedding_cache_dir)
    return EMBEDDER


# 做什么：批量生成切片向量，文档侧不加查询指令
# 入参：切片正文列表
# 返回：与输入等长的向量列表；模型异常直接抛出
def embed_passages(texts: Sequence[str]) -> list[list[float]]:
    settings = get_settings()
    embedder = get_embedder()
    vectors = embedder.passage_embed(list(texts), batch_size=settings.embedding_batch_size)
    return [[float(value) for value in vector] for vector in vectors]


# 做什么：生成查询向量，使用模型自带的查询指令
# 入参：用户问题
# 返回：单条向量；模型异常直接抛出
def embed_query(text: str) -> list[float]:
    embedder = get_embedder()
    vector = next(iter(embedder.query_embed([text])))
    return [float(value) for value in vector]


# 做什么：集合不存在时创建，并给过滤字段建 payload 索引
# 入参：可注入的客户端，测试可传内存实现
# 返回：无；Qdrant 异常直接抛出
def ensure_collection(client: QdrantClient | None = None) -> None:
    settings = get_settings()
    qdrant = client or get_client()
    if not qdrant.collection_exists(settings.qdrant_collection):
        qdrant.create_collection(
            settings.qdrant_collection,
            vectors_config=models.VectorParams(size=settings.embedding_dim, distance=models.Distance.COSINE),
        )
        logger.info("向量集合已创建 | collection=%s | dim=%s", settings.qdrant_collection, settings.embedding_dim)
    for field in ("owner_id", "knowledge_base_id", "document_id"):
        qdrant.create_payload_index(
            settings.qdrant_collection, field_name=field, field_schema=models.PayloadSchemaType.INTEGER,
        )


# 做什么：把一条文档的有效切片写入向量集合，按切片编号幂等覆盖
# 入参：数据库会话、文档所属库/用户/文档编号和切片行
# 返回：写入条数；模型或 Qdrant 异常直接抛出
def upsert_document_chunks(
    db_session: Session, knowledge_base_id: int, owner_id: int, document_id: int,
    chunks: Sequence[KnowledgeChunk], client: QdrantClient | None = None,
) -> int:
    if not chunks:
        return 0
    qdrant = client or get_client()
    ensure_collection(qdrant)
    settings = get_settings()
    vectors = embed_passages([chunk.content for chunk in chunks])
    points = [
        models.PointStruct(
            id=chunk.id, vector=vector,
            payload={
                "owner_id": owner_id, "knowledge_base_id": knowledge_base_id, "document_id": document_id,
                "chunk_id": chunk.id, "chunk_index": chunk.chunk_index,
            },
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    for start in range(0, len(points), UPSERT_BATCH):
        qdrant.upsert(settings.qdrant_collection, points=points[start:start + UPSERT_BATCH], wait=True)
    return len(points)


# 做什么：从数据库读取一条文档的有效切片并同步到向量集合
# 入参：数据库会话、库编号、用户编号和文档编号
# 返回：写入条数；未启用时返回 0
def sync_document(
    db_session: Session, knowledge_base_id: int, owner_id: int, document_id: int,
    client: QdrantClient | None = None,
) -> int:
    chunks = db_session.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.owner_id == owner_id,
        KnowledgeChunk.document_id == document_id,
        KnowledgeChunk.is_deleted == IS_NOT_DELETED,
    ).order_by(KnowledgeChunk.chunk_index)).all()
    return upsert_document_chunks(db_session, knowledge_base_id, owner_id, document_id, chunks, client)


# 做什么：按文档删除向量点，数据库软删后调用
# 入参：文档编号和可注入客户端
# 返回：无；Qdrant 异常直接抛出，由调用方记录后留给重建脚本
def delete_document(document_id: int, client: QdrantClient | None = None) -> None:
    settings = get_settings()
    qdrant = client or get_client()
    if not qdrant.collection_exists(settings.qdrant_collection):
        return
    qdrant.delete(
        settings.qdrant_collection, wait=True,
        points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
        ])),
    )


# 做什么：按知识库删除全部向量点，删库后调用
# 入参：知识库编号和可注入客户端
# 返回：无；Qdrant 异常直接抛出
def delete_knowledge_base(knowledge_base_id: int, client: QdrantClient | None = None) -> None:
    settings = get_settings()
    qdrant = client or get_client()
    if not qdrant.collection_exists(settings.qdrant_collection):
        return
    qdrant.delete(
        settings.qdrant_collection, wait=True,
        points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="knowledge_base_id", match=models.MatchValue(value=knowledge_base_id)),
        ])),
    )


# 做什么：按用户和库过滤后做相似度检索，只返回切片编号和分数
# 入参：用户编号、库编号、查询向量、返回条数及可注入客户端
# 返回：按相似度降序的命中；空集合返回空列表
def search_vectors(
    owner_id: int, knowledge_base_id: int, query_vector: list[float], limit: int,
    client: QdrantClient | None = None,
) -> list[VectorHit]:
    settings = get_settings()
    qdrant = client or get_client()
    if not qdrant.collection_exists(settings.qdrant_collection):
        return []
    hits = qdrant.query_points(
        settings.qdrant_collection, query=query_vector, limit=limit, with_payload=False, with_vectors=False,
        query_filter=models.Filter(must=[
            models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id)),
            models.FieldCondition(key="knowledge_base_id", match=models.MatchValue(value=knowledge_base_id)),
        ]),
    ).points
    return [VectorHit(chunk_id=int(hit.id), score=float(hit.score)) for hit in hits]


# 做什么：统计索引中的向量点数量，供重建脚本和自检使用
# 入参：可选客户端
# 返回：点数量；集合不存在时为 0
def count_points(client: QdrantClient | None = None) -> int:
    settings = get_settings()
    qdrant = client or get_client()
    if not qdrant.collection_exists(settings.qdrant_collection):
        return 0
    return qdrant.count(settings.qdrant_collection, exact=True).count
