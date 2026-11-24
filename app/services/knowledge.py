"""个人知识库与文档的创建、分页查询和检索。"""
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.errors import KnowledgeBaseNotFoundError
from app.core.knowledge import DocumentStatus
from app.models.knowledge import IS_NOT_DELETED, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag import retrieval
from app.rag.chunking import split_document
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseItem,
    KnowledgeBaseListQuery,
    KnowledgeBaseListResponse,
    KnowledgeChunkSearchItem,
    KnowledgeChunkSearchRequest,
    KnowledgeChunkSearchResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentItem,
    KnowledgeDocumentListResponse,
)


# 做什么：为当前用户创建知识库，并读取数据库生成的编号与创建时间
# 入参：独立写入会话、当前用户编号和已校验的建库参数
# 返回：新库的公开字段；写入失败时回滚并抛出异常
def create_knowledge_base(
    db_session: Session, owner_id: int, payload: KnowledgeBaseCreate
) -> KnowledgeBaseItem:
    with db_session.begin():
        knowledge_base = KnowledgeBase(
            owner_id=owner_id, name=payload.name, description=payload.description
        )
        db_session.add(knowledge_base)
        db_session.flush()
        knowledge_base_item = KnowledgeBaseItem.model_validate(knowledge_base)
    return knowledge_base_item


# 做什么：按编号倒序列出当前用户的未删除知识库
# 入参：请求级会话、当前用户编号和已校验的分页参数
# 返回：知识库条目、总数和分页信息；数据库异常直接抛出
def list_knowledge_bases(
    db_session: Session, owner_id: int, query: KnowledgeBaseListQuery
) -> KnowledgeBaseListResponse:
    owner_filter = and_(
        KnowledgeBase.owner_id == owner_id, KnowledgeBase.is_deleted == IS_NOT_DELETED
    )
    count_query = select(func.count()).select_from(KnowledgeBase).where(owner_filter)
    total = db_session.execute(count_query).scalar_one()
    offset_count = (query.page - 1) * query.page_size
    knowledge_base_items: list[KnowledgeBaseItem] = []
    # 总数已能确认空页，跳过明细查询，也避免把超大偏移量交给数据库。
    if offset_count < total:
        item_query = select(
            KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.description, KnowledgeBase.created_at
        ).where(owner_filter)
        item_query = item_query.order_by(KnowledgeBase.id.desc()).offset(offset_count)
        item_rows = db_session.execute(item_query.limit(query.page_size)).mappings().all()
        knowledge_base_items = [KnowledgeBaseItem.model_validate(item_row) for item_row in item_rows]
        knowledge_base_ids = [knowledge_base.id for knowledge_base in knowledge_base_items]
        counts_by_base = _count_documents_by_base(db_session, owner_id, knowledge_base_ids)
        for knowledge_base in knowledge_base_items:
            knowledge_base.document_count = counts_by_base.get(knowledge_base.id, 0)
    return KnowledgeBaseListResponse(
        items=knowledge_base_items, total=total, page=query.page, page_size=query.page_size
    )


# 做什么：一次统计当前页各库的未删除文档数
# 入参：数据库会话、当前用户编号和当前页的知识库编号
# 返回：库编号到文档数的映射；空页返回空字典，数据库异常直接抛出
def _count_documents_by_base(
    db_session: Session, owner_id: int, knowledge_base_ids: list[int]
) -> dict[int, int]:
    if not knowledge_base_ids:
        return {}
    # 新手注意：GROUP BY 像把票据按库放进盒子，每盒只报一个总数。
    count_query = select(KnowledgeDocument.knowledge_base_id, func.count()).where(
        KnowledgeDocument.knowledge_base_id.in_(knowledge_base_ids),
        KnowledgeDocument.owner_id == owner_id,
        KnowledgeDocument.is_deleted == IS_NOT_DELETED,
    ).group_by(KnowledgeDocument.knowledge_base_id)
    count_rows = db_session.execute(count_query).all()
    return {base_id: document_count for base_id, document_count in count_rows}


# 做什么：确认指定知识库存在、未删除且属于当前用户
# 入参：数据库会话、当前用户编号和路径中的知识库编号
# 返回：无；归属校验失败时抛 KnowledgeBaseNotFoundError
def _require_owned_knowledge_base(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    base_query = select(KnowledgeBase.id).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == owner_id,
        KnowledgeBase.is_deleted == IS_NOT_DELETED,
    )
    if db_session.scalar(base_query) is None:
        raise KnowledgeBaseNotFoundError()


# 做什么：向其他服务提供本人知识库的可见性校验
# 入参：数据库会话、当前用户编号和知识库编号
# 返回：无；知识库不可见时抛 KnowledgeBaseNotFoundError
def require_owned_knowledge_base(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    _require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)


# 做什么：校验归属，在同一事务保存原文与切片并标记文档就绪
# 入参：独立写入会话、当前用户编号、知识库编号和已校验表单
# 返回：文档的公开字段；校验或写入失败时回滚并抛出异常
def create_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, payload: KnowledgeDocumentCreate
) -> KnowledgeDocumentItem:
    with db_session.begin():
        _require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base_id,
            owner_id=owner_id,
            title=payload.title,
            raw_content=payload.content,
            char_count=len(payload.content),
            status=DocumentStatus.CHUNKING,
        )
        db_session.add(document)
        db_session.flush()
        _save_document_chunks(db_session, document)
        document.status = DocumentStatus.READY
        document_item = KnowledgeDocumentItem.model_validate(document)
    return document_item


# 做什么：切分正文并批量写入当前文档的切片
# 入参：写入事务的会话，已取得编号的文档
# 返回：无；无切片时写入失败状态并抛出系统异常，外层事务回滚
def _save_document_chunks(db_session: Session, document: KnowledgeDocument) -> None:
    chunk_drafts = split_document(document.title, document.raw_content)
    # 无可用切片的文档不能进入就绪状态，失败状态随事务一同回滚。
    if not chunk_drafts:
        document.status = DocumentStatus.FAILED
        db_session.flush()
        raise RuntimeError("文档切分未生成切片")
    chunks: list[KnowledgeChunk] = []
    for chunk_index, draft in enumerate(chunk_drafts):
        chunk = KnowledgeChunk(
            knowledge_base_id=document.knowledge_base_id,
            document_id=document.id,
            owner_id=document.owner_id,
            chunk_index=chunk_index,
            heading_path=draft.heading_path,
            content=draft.content,
            char_start=draft.char_start,
            char_end=draft.char_end,
        )
        chunks.append(chunk)
    db_session.add_all(chunks)
    db_session.flush()


# 做什么：校验归属后按编号倒序分页读取该库文档的公开字段
# 入参：请求级会话、当前用户编号、知识库编号和已校验的分页参数
# 返回：文档条目、总数和分页信息；校验或查询失败时抛出异常
def list_knowledge_documents(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: KnowledgeBaseListQuery
) -> KnowledgeDocumentListResponse:
    _require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)
    document_filter = and_(
        KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        KnowledgeDocument.owner_id == owner_id,
        KnowledgeDocument.is_deleted == IS_NOT_DELETED,
    )
    count_query = select(func.count()).select_from(KnowledgeDocument).where(document_filter)
    total = db_session.execute(count_query).scalar_one()
    offset_count = (query.page - 1) * query.page_size
    document_items: list[KnowledgeDocumentItem] = []
    # 空页只需要总数，跳过明细查询。
    if offset_count < total:
        item_query = select(
            KnowledgeDocument.id, KnowledgeDocument.title, KnowledgeDocument.char_count,
            KnowledgeDocument.status, KnowledgeDocument.created_at,
        ).where(document_filter)
        item_query = item_query.order_by(KnowledgeDocument.id.desc()).offset(offset_count)
        item_rows = db_session.execute(item_query.limit(query.page_size)).mappings().all()
        document_items = [KnowledgeDocumentItem.model_validate(item_row) for item_row in item_rows]
    return KnowledgeDocumentListResponse(
        items=document_items, total=total, page=query.page, page_size=query.page_size
    )


# 做什么：检查知识库归属后检索切片，组装命中的公开字段
# 入参：请求级会话、当前用户编号、知识库编号和已校验的搜索请求
# 返回：切片命中列表；知识库不可见时抛 KnowledgeBaseNotFoundError
def search_knowledge_chunks(
    db_session: Session, owner_id: int, knowledge_base_id: int, payload: KnowledgeChunkSearchRequest
) -> KnowledgeChunkSearchResponse:
    _require_owned_knowledge_base(db_session, owner_id, knowledge_base_id)
    chunk_rows = retrieval.search_chunks(
        db_session, owner_id, knowledge_base_id, payload.query, payload.top_k
    )
    items = [KnowledgeChunkSearchItem.model_validate(chunk_row) for chunk_row in chunk_rows]
    return KnowledgeChunkSearchResponse(items=items)
