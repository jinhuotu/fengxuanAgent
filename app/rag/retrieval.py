"""构造并执行 MySQL 知识库切片全文查询。"""
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Float, Integer, Select, String, bindparam, literal_column, select, text, type_coerce
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.knowledge import SEARCH_CANDIDATE_MIN
from app.core.logging import get_logger
from app.models.knowledge import IS_NOT_DELETED, KnowledgeChunk
from app.rag.relevance import choose_probe, extract_probe_terms, select_relevant_hits

NATURAL_LANGUAGE_MATCH = "MATCH (knowledge_chunks.content) AGAINST (:term IN NATURAL LANGUAGE MODE)"
logger = get_logger(__name__)


@dataclass(frozen=True)
class RelevantSearchResult:
    hits: list[RowMapping]
    candidates: int
    dropped: int


# 做什么：构造指定用户和知识库的全文查询，包括相关性排序和返回条数
# 入参：归属用户编号、知识库编号、已校验的查询词和条数
# 返回：带绑定参数的 Select，不访问数据库
def build_search_statement(owner_id: int, knowledge_base_id: int, query: str, top_k: int) -> Select:
    term = bindparam("term", value=query, type_=String())
    # 新手注意：绑定参数像表格的填空，查询词只进空格，不能改动 SQL 句式。
    match_expression = type_coerce(text(NATURAL_LANGUAGE_MATCH).bindparams(term), Float())
    return select(
        KnowledgeChunk.id.label("chunk_id"),
        KnowledgeChunk.document_id,
        KnowledgeChunk.heading_path,
        KnowledgeChunk.content,
        KnowledgeChunk.char_start,
        KnowledgeChunk.char_end,
        match_expression.label("score"),
    ).where(
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.owner_id == owner_id,
        KnowledgeChunk.is_deleted == IS_NOT_DELETED,
        match_expression,
    ).order_by(
        literal_column("score").desc(), KnowledgeChunk.id.asc(),
    ).limit(bindparam("top_k", value=top_k, type_=Integer()))


# 做什么：执行全文查询并读取命中的行映射
# 入参：数据库会话、归属用户编号、知识库编号、查询词、结果条数及可选候选上限
# 返回：带字段名的命中行，无命中返回空序列，数据库异常直接抛出
def search_chunks(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: str, top_k: int,
    *, candidate_limit: int | None = None,
) -> Sequence[RowMapping]:
    row_limit = top_k if candidate_limit is None else candidate_limit
    statement = build_search_statement(owner_id, knowledge_base_id, query, row_limit)
    return db_session.execute(statement).mappings().all()


# 做什么：多取一次全文候选，在内存过滤并截断，记录结果数量
# 入参：数据库会话、归属用户编号、知识库编号、查询词和结果上限
# 返回：保留的命中、原始候选条数及未入选条数；数据库异常直接抛出
def search_relevant_chunks(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: str, top_k: int,
) -> RelevantSearchResult:
    settings = get_settings()
    candidate_limit = min(
        settings.search_candidate_max, max(SEARCH_CANDIDATE_MIN, top_k * settings.search_candidate_multiplier),
    )
    candidates = search_chunks(db_session, owner_id, knowledge_base_id, query, top_k, candidate_limit=candidate_limit)
    # 长问句剥不出短词时改从候选正文里挑片段，短问句仍用原词。
    probe = choose_probe(query, [candidate["content"] for candidate in candidates])
    direct_probe = extract_probe_terms(query)
    probe_source = "无"
    if probe:
        probe_source = "句式" if probe == direct_probe else "滑窗"
    hits = select_relevant_hits(
        candidates, top_k, settings.search_min_score_ratio, settings.search_min_score_absolute, probe=probe,
    )
    candidate_count = len(candidates)
    dropped = candidate_count - len(hits)
    probe_matches = [hit for hit in hits if probe and probe in hit["content"]]
    logger.info("知识库检索过滤 | knowledge_base_id=%s | 候选=%s | 保留=%s | 丢弃=%s | 探针命中=%s | 探针来源=%s",
                knowledge_base_id, candidate_count, len(hits), dropped, len(probe_matches), probe_source)
    return RelevantSearchResult(hits=hits, candidates=candidate_count, dropped=dropped)
