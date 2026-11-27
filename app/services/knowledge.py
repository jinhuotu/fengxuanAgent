"""个人知识库与文档的创建、分页、删除和检索。"""
from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import KnowledgeChunkNotFoundError, KnowledgeDocumentNotFoundError
from app.core.knowledge import (
    DISABLED_KIND_NOTES, ENABLED_FILE_KINDS, FILE_EXTENSION_KINDS, FILE_KIND_NAMES, SOFT_DELETED,
)
from app.core.logging import get_logger
from app.models.chat import ChatSession
from app.models.knowledge import IS_NOT_DELETED, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag import retrieval
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseDeleteResult,
    KnowledgeBaseItem,
    KnowledgeBaseListQuery,
    KnowledgeBaseListResponse,
    KnowledgeChunkDetail,
    KnowledgeChunkItem,
    KnowledgeChunkListResponse,
    KnowledgeChunkSearchItem,
    KnowledgeChunkSearchRequest,
    KnowledgeChunkSearchResponse,
    KnowledgeDocumentDeleteResult,
    KnowledgeDocumentItem,
    KnowledgeDocumentListResponse,
    KnowledgeFileTypeItem,
)
from app.services import knowledge_document_store as document_store
from app.services.knowledge_document_store import (
    create_knowledge_document,  # noqa: F401  # 兼容原有调用方，实现已迁到 knowledge_document_store
    require_owned_base,
)

logger = get_logger(__name__)


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


# 做什么：生成指定库的归属和有效状态条件
# 入参：当前用户编号和知识库编号
# 返回：供读取和写入复用的 SQL 条件，不访问数据库
def _owned_base_filter(owner_id: int, knowledge_base_id: int) -> ColumnElement[bool]:
    return and_(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == owner_id,
        KnowledgeBase.is_deleted == IS_NOT_DELETED,
    )


# 做什么：向其他写服务提供本人知识库的可见性校验与行锁
# 入参：数据库会话、当前用户编号和知识库编号
# 返回：无；知识库不可见时抛 KnowledgeBaseNotFoundError
def require_owned_knowledge_base(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    require_owned_base(db_session, owner_id, knowledge_base_id, lock=True)


# 做什么：定位指定库内属于当前用户的有效文档
# 入参：当前用户编号和库编号
# 返回：文档列表、校验和删除共用的 SQL 条件，不访问数据库
def _owned_documents_filter(owner_id: int, knowledge_base_id: int) -> ColumnElement[bool]:
    return and_(
        KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        KnowledgeDocument.owner_id == owner_id,
        KnowledgeDocument.is_deleted == IS_NOT_DELETED,
    )


# 做什么：检查文档范围，并提供后续文档更新所需的条件
# 入参：会话、当前用户编号、库编号和文档编号
# 返回：文档 SQL 条件；文档不可见时抛 KnowledgeDocumentNotFoundError
def _require_owned_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, document_id: int,
) -> ColumnElement[bool]:
    document_filter = and_(_owned_documents_filter(owner_id, knowledge_base_id), KnowledgeDocument.id == document_id)
    if db_session.scalar(select(KnowledgeDocument.id).where(document_filter)) is None:
        raise KnowledgeDocumentNotFoundError()
    return document_filter


# 做什么：限定切片归属、库编号和有效状态
# 入参：当前用户编号和知识库编号
# 返回：切片分页和删除共用的 SQL 条件，不访问数据库
def _owned_chunks_filter(owner_id: int, knowledge_base_id: int) -> ColumnElement[bool]:
    return and_(
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.owner_id == owner_id,
        KnowledgeChunk.is_deleted == IS_NOT_DELETED,
    )


# 做什么：清除当前用户有效会话对即将删除的库的引用
# 入参：删除事务的会话、当前用户编号和库编号
# 返回：修改的会话数；失败时抛异常并由外层事务回滚
def _clear_selected_knowledge_base(db_session: Session, owner_id: int, knowledge_base_id: int) -> int:
    # 选库变动不影响会话排序，显式保留原时间避免触发 onupdate。
    clear_query = update(ChatSession).where(
        ChatSession.user_id == owner_id,
        ChatSession.knowledge_base_id == knowledge_base_id,
        ChatSession.is_deleted == IS_NOT_DELETED,
    ).values(knowledge_base_id=None, updated_at=ChatSession.updated_at)
    return db_session.execute(clear_query).rowcount


# 做什么：在同一事务软删本人知识库、文档和切片，并清理有效会话选库
# 入参：独立写会话、当前用户编号和库编号
# 返回：删除结果计数；库不可见或写入失败时回滚并抛相应异常
def delete_knowledge_base(db_session: Session, owner_id: int, knowledge_base_id: int) -> KnowledgeBaseDeleteResult:
    with db_session.begin():
        require_owned_base(db_session, owner_id, knowledge_base_id, lock=True)
        document_filter = _owned_documents_filter(owner_id, knowledge_base_id)
        # 更新前记下这批文档，避免把此前已删除的文档重新计入本次操作。
        document_ids = db_session.scalars(select(KnowledgeDocument.id).where(document_filter)).all()
        document_update = update(KnowledgeDocument).where(document_filter).values(is_deleted=SOFT_DELETED)
        deleted_documents = db_session.execute(document_update).rowcount
        chunk_update = update(KnowledgeChunk).where(
            _owned_chunks_filter(owner_id, knowledge_base_id), KnowledgeChunk.document_id.in_(document_ids),
        ).values(is_deleted=SOFT_DELETED)
        deleted_chunks = db_session.execute(chunk_update).rowcount
        base_update = update(KnowledgeBase).where(_owned_base_filter(owner_id, knowledge_base_id))
        db_session.execute(base_update.values(is_deleted=SOFT_DELETED))
        cleared_sessions = _clear_selected_knowledge_base(db_session, owner_id, knowledge_base_id)
        result = KnowledgeBaseDeleteResult(
            id=knowledge_base_id, deleted_documents=deleted_documents,
            deleted_chunks=deleted_chunks, cleared_sessions=cleared_sessions,
        )
    document_store.delete_vector_index_vectors(knowledge_base_id=knowledge_base_id)
    document_store.verify_search_index_after_delete(db_session, owner_id, knowledge_base_id)
    return result


# 做什么：软删指定文档及其有效切片
# 入参：独立写会话、当前用户编号、库编号和文档编号
# 返回：文档编号和切片数；库或文档不可见、写入失败时抛相应异常
def delete_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, document_id: int,
) -> KnowledgeDocumentDeleteResult:
    with db_session.begin():
        require_owned_base(db_session, owner_id, knowledge_base_id, lock=True)
        document_filter = _require_owned_document(db_session, owner_id, knowledge_base_id, document_id)
        document_update = update(KnowledgeDocument).where(document_filter).values(is_deleted=SOFT_DELETED)
        db_session.execute(document_update)
        chunk_update = update(KnowledgeChunk).where(
            _owned_chunks_filter(owner_id, knowledge_base_id), KnowledgeChunk.document_id == document_id,
        ).values(is_deleted=SOFT_DELETED)
        result = KnowledgeDocumentDeleteResult(id=document_id, deleted_chunks=db_session.execute(chunk_update).rowcount)
    document_store.delete_vector_index_vectors(document_id=document_id)
    document_store.verify_search_index_after_delete(db_session, owner_id, knowledge_base_id)
    return result


# 做什么：按原文顺序分页列出本人文档的有效切片
# 入参：会话、用户编号、库编号、文档编号和分页参数
# 返回：正文片段与偏移的分页结果；库或文档不可见时抛对应异常
def list_knowledge_document_chunks(
    db_session: Session, owner_id: int, knowledge_base_id: int, document_id: int, query: KnowledgeBaseListQuery,
) -> KnowledgeChunkListResponse:
    require_owned_base(db_session, owner_id, knowledge_base_id)
    _require_owned_document(db_session, owner_id, knowledge_base_id, document_id)
    chunk_filter = and_(_owned_chunks_filter(owner_id, knowledge_base_id), KnowledgeChunk.document_id == document_id)
    count_query = select(func.count()).select_from(KnowledgeChunk).where(chunk_filter)
    total = db_session.execute(count_query).scalar_one()
    offset_count = (query.page - 1) * query.page_size
    items: list[KnowledgeChunkItem] = []
    # 空页只需要总数，跳过明细查询。
    if offset_count < total:
        item_query = select(
            KnowledgeChunk.id, KnowledgeChunk.chunk_index, KnowledgeChunk.heading_path,
            KnowledgeChunk.char_start, KnowledgeChunk.char_end, KnowledgeChunk.content,
        ).where(chunk_filter).order_by(KnowledgeChunk.chunk_index).offset(offset_count).limit(query.page_size)
        item_rows = db_session.execute(item_query).mappings().all()
        items = [KnowledgeChunkItem.model_validate(item_row) for item_row in item_rows]
    return KnowledgeChunkListResponse(items=items, total=total, page=query.page, page_size=query.page_size)


# 做什么：按全局切片编号读取本人切片及所属文档标题
# 入参：请求级数据库会话、当前用户和切片编号
# 返回：切片详情；切片或所属文档不可见时抛 KnowledgeChunkNotFoundError
def get_knowledge_chunk(db_session: Session, owner_id: int, chunk_id: int) -> KnowledgeChunkDetail:
    chunk = db_session.scalar(select(KnowledgeChunk).where(
        KnowledgeChunk.id == chunk_id, KnowledgeChunk.owner_id == owner_id, KnowledgeChunk.is_deleted == IS_NOT_DELETED,
    ))
    if chunk is None:
        raise KnowledgeChunkNotFoundError()
    document_title = db_session.scalar(select(KnowledgeDocument.title).where(
        KnowledgeDocument.id == chunk.document_id, KnowledgeDocument.owner_id == owner_id, KnowledgeDocument.is_deleted == IS_NOT_DELETED,
    ))
    if document_title is None:
        raise KnowledgeChunkNotFoundError()
    return KnowledgeChunkDetail(
        chunk_id=chunk.id, knowledge_base_id=chunk.knowledge_base_id, document_id=chunk.document_id,
        document_title=document_title, chunk_index=chunk.chunk_index, heading_path=chunk.heading_path,
        char_start=chunk.char_start, char_end=chunk.char_end, content=chunk.content,
    )



# 做什么：校验归属后按编号倒序分页读取该库文档的公开字段
# 入参：请求级会话、当前用户编号、知识库编号和已校验的分页参数
# 返回：文档条目、总数和分页信息；校验或查询失败时抛出异常
def list_knowledge_documents(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: KnowledgeBaseListQuery
) -> KnowledgeDocumentListResponse:
    require_owned_base(db_session, owner_id, knowledge_base_id)
    document_filter = _owned_documents_filter(owner_id, knowledge_base_id)
    count_query = select(func.count()).select_from(KnowledgeDocument).where(document_filter)
    total = db_session.execute(count_query).scalar_one()
    offset_count = (query.page - 1) * query.page_size
    document_items: list[KnowledgeDocumentItem] = []
    # 空页只需要总数，跳过明细查询。
    if offset_count < total:
        item_query = select(
            KnowledgeDocument.id, KnowledgeDocument.title, KnowledgeDocument.char_count,
            KnowledgeDocument.status, KnowledgeDocument.source_type, KnowledgeDocument.file_type,
            KnowledgeDocument.file_name, KnowledgeDocument.file_size, KnowledgeDocument.created_at,
        ).where(document_filter)
        item_query = item_query.order_by(KnowledgeDocument.id.desc()).offset(offset_count)
        item_rows = db_session.execute(item_query.limit(query.page_size)).mappings().all()
        document_items = [KnowledgeDocumentItem.model_validate(item_row) for item_row in item_rows]
    return KnowledgeDocumentListResponse(
        items=document_items, total=total, page=query.page, page_size=query.page_size
    )


# 做什么：检查知识库归属后检索切片，组装命中的公开字段
# 入参：请求级会话、当前用户编号、知识库编号和已校验的搜索请求
# 返回：切片命中列表和候选、丢弃计数；知识库不可见时抛 KnowledgeBaseNotFoundError
def search_knowledge_chunks(
    db_session: Session, owner_id: int, knowledge_base_id: int, payload: KnowledgeChunkSearchRequest
) -> KnowledgeChunkSearchResponse:
    require_owned_base(db_session, owner_id, knowledge_base_id)
    result = retrieval.search_relevant_chunks(
        db_session, owner_id, knowledge_base_id, payload.query, payload.top_k
    )
    items = [KnowledgeChunkSearchItem.model_validate(chunk_row) for chunk_row in result.hits]
    return KnowledgeChunkSearchResponse(items=items, candidates=result.candidates, dropped=result.dropped)


# 做什么：把后端真实支持的文件类型整理成前端类型卡片清单
# 入参：无
# 返回：按编号排序的类型条目，未启用的类型附带说明
def list_file_types() -> list[KnowledgeFileTypeItem]:
    extensions_by_kind: dict[int, list[str]] = {}
    for extension, file_kind in FILE_EXTENSION_KINDS.items():
        extensions_by_kind.setdefault(file_kind, []).append(extension)
    items: list[KnowledgeFileTypeItem] = []
    for file_kind in sorted(FILE_KIND_NAMES):
        enabled = file_kind in ENABLED_FILE_KINDS
        items.append(
            KnowledgeFileTypeItem(
                kind=int(file_kind), name=FILE_KIND_NAMES[file_kind],
                extensions=sorted(extensions_by_kind.get(file_kind, [])),
                enabled=enabled, note=None if enabled else DISABLED_KIND_NOTES.get(file_kind),
            )
        )
    return items
