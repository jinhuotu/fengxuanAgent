"""文档落库与索引维护：创建、预切块写入、向量同步与索引自检。"""
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import KnowledgeBaseNotFoundError
from app.core.knowledge import (
    INDEX_PROBE_MAX_CHARS, INDEX_PROBE_RETRY_SECONDS, INDEX_PROBE_TOP_K,
    SEARCH_QUERY_MIN_CHARS, DocumentSource, DocumentStatus, FileKind,
)
from app.core.logging import get_logger
from app.models.knowledge import IS_NOT_DELETED, KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.rag import retrieval, vector_index
from app.rag.chunking import ChunkDraft, split_document
from app.rag.index_maintenance import MYSQL_DIALECT, rebuild_fulltext_index
from app.schemas.knowledge import KnowledgeDocumentCreate, KnowledgeDocumentItem
from app.services.knowledge_channel_common import ParsedFile

logger = get_logger(__name__)


# 做什么：确认知识库属于当前用户且未删除
# 入参：数据库会话、用户编号、库编号和是否加行锁
# 返回：无；不存在或不可见时抛 404/4001
def require_owned_base(
    db_session: Session, owner_id: int, knowledge_base_id: int, lock: bool = False,
) -> None:
    statement = select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.owner_id == owner_id,
        KnowledgeBase.is_deleted == IS_NOT_DELETED,
    )
    if lock:
        statement = statement.with_for_update()
    if db_session.scalar(statement) is None:
        raise KnowledgeBaseNotFoundError()


# 做什么：把一份粘贴文本按通用规则切分后入库
# 入参：独立写入会话、用户编号、库编号和已校验的请求
# 返回：文档公开字段；校验或写入失败时回滚并抛出异常
def create_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, payload: KnowledgeDocumentCreate
) -> KnowledgeDocumentItem:
    return persist_document(
        db_session, owner_id, knowledge_base_id,
        title=payload.title, content=payload.content, drafts=None,
        source_type=DocumentSource.PASTE, file_kind=FileKind.TEXT,
        file_name=None, file_size=None,
    )


# 做什么：把通道解析好的文件按预切块入库，不再次切分
# 入参：独立写入会话、用户编号、库编号、标题、解析结果和文件信息
# 返回：文档公开字段；校验或写入失败时回滚并抛出异常
def create_file_knowledge_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, *,
    title: str, parsed: ParsedFile, file_kind: int, file_name: str, file_size: int,
) -> KnowledgeDocumentItem:
    return persist_document(
        db_session, owner_id, knowledge_base_id,
        title=title, content=parsed.text, drafts=parsed.drafts,
        source_type=DocumentSource.FILE, file_kind=file_kind,
        file_name=file_name, file_size=file_size,
    )


# 做什么：在一个事务里建文档、写切片、置为可检索，再同步向量与自检
# 入参：会话、用户与库编号、标题、原文、可选预切块、来源与文件信息
# 返回：文档公开字段；无切片时抛系统异常并回滚
def persist_document(
    db_session: Session, owner_id: int, knowledge_base_id: int, *,
    title: str, content: str, drafts: list[ChunkDraft] | None,
    source_type: int, file_kind: int, file_name: str | None, file_size: int | None,
) -> KnowledgeDocumentItem:
    with db_session.begin():
        require_owned_base(db_session, owner_id, knowledge_base_id, lock=True)
        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base_id,
            owner_id=owner_id,
            title=title,
            raw_content=content,
            char_count=len(content),
            status=DocumentStatus.CHUNKING,
            source_type=source_type,
            file_type=file_kind,
            file_name=file_name,
            file_size=file_size,
        )
        db_session.add(document)
        db_session.flush()
        chunk_drafts = save_document_chunks(db_session, document, drafts)
        document.status = DocumentStatus.READY
        document_item = KnowledgeDocumentItem.model_validate(document)
    sync_vector_index_after_upload(db_session, owner_id, knowledge_base_id, document_item.id)
    verify_search_index_after_upload(db_session, owner_id, knowledge_base_id, chunk_drafts)
    return document_item


# 做什么：写入切片；没有预切块时按通用规则切分
# 入参：写入事务的会话、已取得编号的文档、可选预切块
# 返回：实际写入的切片草稿；无切片时写入失败状态并抛出系统异常
def save_document_chunks(
    db_session: Session, document: KnowledgeDocument, drafts: list[ChunkDraft] | None,
) -> list[ChunkDraft]:
    chunk_drafts = drafts if drafts is not None else split_document(document.title, document.raw_content)
    if not chunk_drafts:
        document.status = DocumentStatus.FAILED
        db_session.flush()
        raise RuntimeError("文档切分未生成切片")
    chunks: list[KnowledgeChunk] = []
    for chunk_index, draft in enumerate(chunk_drafts):
        chunks.append(
            KnowledgeChunk(
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                owner_id=document.owner_id,
                chunk_index=chunk_index,
                heading_path=draft.heading_path,
                content=draft.content,
                char_start=draft.char_start,
                char_end=draft.char_end,
            )
        )
    db_session.add_all(chunks)
    db_session.flush()
    return chunk_drafts


# 做什么：上传提交后把新切片写入向量集合，失败只记日志
# 入参：已提交的会话、用户编号、库编号和文档编号
# 返回：无；数据库仍是事实来源，索引可由重建脚本补齐
def sync_vector_index_after_upload(
    db_session: Session, owner_id: int, knowledge_base_id: int, document_id: int,
) -> None:
    if not vector_index.is_enabled():
        return
    try:
        # 新手注意：只读查询也会自动开事务；不收尾的话，后续全文自检再开事务会报
        # "A transaction is already begun"，自检等于被静默跳过。这里用 with 保证查完即结束事务。
        with db_session.begin():
            written = vector_index.sync_document(db_session, knowledge_base_id, owner_id, document_id)
        logger.info("向量索引同步 | owner_id=%s | knowledge_base_id=%s | document_id=%s | points=%s",
                    owner_id, knowledge_base_id, document_id, written)
    except Exception as error:
        logger.warning("向量索引同步失败 | owner_id=%s | knowledge_base_id=%s | document_id=%s | error_type=%s | 需重建补齐",
                       owner_id, knowledge_base_id, document_id, type(error).__name__)


# 做什么：删除提交后移除对应向量点，陈旧点也会在检索时被回库校验挡掉
# 入参：文档编号或知识库编号，二选一
# 返回：无；失败只记日志
def delete_vector_index_vectors(*, document_id: int | None = None, knowledge_base_id: int | None = None) -> None:
    if not vector_index.is_enabled() or (document_id is None and knowledge_base_id is None):
        return
    try:
        if document_id is not None:
            vector_index.delete_document(document_id)
            logger.info("向量索引删除 | document_id=%s", document_id)
        else:
            vector_index.delete_knowledge_base(knowledge_base_id or 0)
            logger.info("向量索引删除 | knowledge_base_id=%s", knowledge_base_id)
    except Exception as error:
        logger.warning("向量索引删除失败 | document_id=%s | knowledge_base_id=%s | error_type=%s",
                       document_id, knowledge_base_id, type(error).__name__)


# 做什么：执行一次上传探针，并结束本次读取事务
# 入参：会话、当前用户编号、库编号和探针词
# 返回：命中条数；查询失败时回滚，由自检入口记录异常
def count_index_probe_matches(
    db_session: Session, owner_id: int, knowledge_base_id: int, probe: str,
) -> int:
    # 每轮重开读取事务，等待和重建期间不占用查询连接。
    with db_session.begin():
        return len(retrieval.search_chunks(db_session, owner_id, knowledge_base_id, probe, INDEX_PROBE_TOP_K))


# 做什么：提交后检索首块正文，连续两次未命中时重建并复检一次
# 入参：已提交的写会话、当前用户编号、库编号和刚保存的切片草稿
# 返回：无；自检异常只记录日志，上传结果仍为就绪
def verify_search_index_after_upload(
    db_session: Session, owner_id: int, knowledge_base_id: int, chunks: list[ChunkDraft],
) -> None:
    try:
        if db_session.get_bind().dialect.name != MYSQL_DIALECT:
            return
        probe = chunks[0].content.strip()[:INDEX_PROBE_MAX_CHARS]
        if len(probe) < SEARCH_QUERY_MIN_CHARS:
            logger.info("上传索引自检探针词过短跳过 owner_id=%s knowledge_base_id=%s", owner_id, knowledge_base_id)
            return
        matches = count_index_probe_matches(db_session, owner_id, knowledge_base_id, probe)
        if not matches:
            sleep(INDEX_PROBE_RETRY_SECONDS)
            matches = count_index_probe_matches(db_session, owner_id, knowledge_base_id, probe)
        if not matches:
            rebuild_fulltext_index(db_session)
            matches = count_index_probe_matches(db_session, owner_id, knowledge_base_id, probe)
        if matches:
            logger.info("上传索引自检命中 owner_id=%s knowledge_base_id=%s 命中条数=%s", owner_id, knowledge_base_id, matches)
        else:
            logger.error("文档已就绪但索引未命中，需人工重建 owner_id=%s knowledge_base_id=%s", owner_id, knowledge_base_id)
    except Exception as error:
        logger.warning("上传索引自检异常 owner_id=%s knowledge_base_id=%s error_type=%s",
                       owner_id, knowledge_base_id, type(error).__name__)


# 做什么：删除后确认全文索引仍能命中，只在未命中时重建一次
# 入参：已提交的写会话、当前用户编号和刚删除的库编号
# 返回：无；自检异常只记录日志，删除结果不受影响
def verify_search_index_after_delete(db_session: Session, owner_id: int, knowledge_base_id: int) -> None:
    try:
        if db_session.get_bind().dialect.name != MYSQL_DIALECT:
            return
        # 该库删空时改取本人其他有效切片：全文索引是整表级的，用哪一块探针都可以。
        with db_session.begin():
            probe_row = db_session.execute(
                select(KnowledgeChunk.knowledge_base_id, KnowledgeChunk.content).where(
                    KnowledgeChunk.owner_id == owner_id, KnowledgeChunk.is_deleted == IS_NOT_DELETED,
                ).order_by(KnowledgeChunk.id).limit(1)
            ).first()
        if probe_row is None:
            logger.info("删除后索引自检跳过 owner_id=%s knowledge_base_id=%s 原因=本人已无有效切片",
                        owner_id, knowledge_base_id)
            return
        probe = probe_row.content.strip()[:INDEX_PROBE_MAX_CHARS]
        if len(probe) < SEARCH_QUERY_MIN_CHARS:
            logger.info("删除后索引自检跳过 owner_id=%s knowledge_base_id=%s 原因=探针过短",
                        owner_id, knowledge_base_id)
            return
        probe_base_id = probe_row.knowledge_base_id
        matches = count_index_probe_matches(db_session, owner_id, probe_base_id, probe)
        if not matches:
            sleep(INDEX_PROBE_RETRY_SECONDS)
            matches = count_index_probe_matches(db_session, owner_id, probe_base_id, probe)
        if matches:
            # 索引仍可命中就不做整表重建，避免每次删除都锁表。
            logger.info("删除后索引仍可命中，跳过重建 owner_id=%s knowledge_base_id=%s", owner_id, knowledge_base_id)
            return
        rebuild_fulltext_index(db_session)
        matches = count_index_probe_matches(db_session, owner_id, probe_base_id, probe)
        if matches:
            logger.info("删除后索引未命中并已重建 owner_id=%s knowledge_base_id=%s", owner_id, knowledge_base_id)
        else:
            logger.error("删除后索引仍未命中，需人工重建 owner_id=%s knowledge_base_id=%s", owner_id, knowledge_base_id)
    except Exception as error:
        logger.warning("删除后索引自检异常 owner_id=%s knowledge_base_id=%s error_type=%s",
                       owner_id, knowledge_base_id, type(error).__name__)
