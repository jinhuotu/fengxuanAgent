"""构造并执行 MySQL 知识库切片全文查询。"""
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus

from httpx import TransportError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import Float, Integer, Select, String, bindparam, literal_column, select, text, type_coerce
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.knowledge import SEARCH_CANDIDATE_MIN
from app.core.logging import get_logger
from app.models.knowledge import IS_NOT_DELETED, KnowledgeChunk
from app.rag import vector_index
from app.rag.relevance import choose_probe, extract_probe_terms, select_relevant_hits

NATURAL_LANGUAGE_MATCH = "MATCH (knowledge_chunks.content) AGAINST (:term IN NATURAL LANGUAGE MODE)"
logger = get_logger(__name__)


@dataclass(frozen=True)
class RelevantSearchResult:
    hits: list[RowMapping]
    candidates: int
    dropped: int
    vector_unavailable: bool = False


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


# 做什么：按配置的候选上限计算本次多取的条数
# 入参：目标条数
# 返回：至少 SEARCH_CANDIDATE_MIN、至多 search_candidate_max 的条数
def _candidate_limit(top_k: int) -> int:
    settings = get_settings()
    return min(settings.search_candidate_max, max(SEARCH_CANDIDATE_MIN, top_k * settings.search_candidate_multiplier))


# 做什么：执行原有关键词检索、探针过滤并记录统计
# 入参：数据库会话、归属用户、库编号、查询词、目标条数、候选上限及是否向量降级
# 返回：保留命中、候选数、丢弃数及向量降级状态
def _keyword_search(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: str, top_k: int, candidate_limit: int,
    *, vector_unavailable: bool = False,
) -> RelevantSearchResult:
    candidates = search_chunks(db_session, owner_id, knowledge_base_id, query, top_k, candidate_limit=candidate_limit)
    # 长问句剥不出短词时改从候选正文里挑片段，短问句仍用原词。
    probe = choose_probe(query, [candidate["content"] for candidate in candidates])
    direct_probe = extract_probe_terms(query)
    probe_source = "无"
    if probe:
        probe_source = "句式" if probe == direct_probe else "滑窗"
    hits = select_relevant_hits(
        candidates, top_k, get_settings().search_min_score_ratio,
        get_settings().search_min_score_absolute, probe=probe,
    )
    candidate_count = len(candidates)
    dropped = candidate_count - len(hits)
    probe_matches = [hit for hit in hits if probe and probe in hit["content"]]
    logger.info("知识库检索过滤 | 模式=keyword | knowledge_base_id=%s | 候选=%s | 保留=%s | 丢弃=%s | 探针命中=%s | 探针来源=%s",
                knowledge_base_id, candidate_count, len(hits), dropped, len(probe_matches), probe_source)
    return RelevantSearchResult(hits=hits, candidates=candidate_count, dropped=dropped, vector_unavailable=vector_unavailable)


# 做什么：按切片编号回库校验归属和未删状态，丢弃索引里的陈旧点
# 入参：数据库会话、归属用户、库编号和候选切片编号
# 返回：按传入顺序排好的切片行；不可见的编号被跳过
def _chunk_rows_by_ids(
    db_session: Session, owner_id: int, knowledge_base_id: int, chunk_ids: Sequence[int],
) -> dict[int, RowMapping]:
    if not chunk_ids:
        return {}
    rows = db_session.execute(select(
        KnowledgeChunk.id.label("chunk_id"), KnowledgeChunk.document_id, KnowledgeChunk.heading_path,
        KnowledgeChunk.content, KnowledgeChunk.char_start, KnowledgeChunk.char_end,
    ).where(
        KnowledgeChunk.id.in_(list(chunk_ids)),
        KnowledgeChunk.knowledge_base_id == knowledge_base_id,
        KnowledgeChunk.owner_id == owner_id,
        KnowledgeChunk.is_deleted == IS_NOT_DELETED,
    )).mappings().all()
    return {row["chunk_id"]: row for row in rows}


# 做什么：向量召回后回库校验，并把相似度写成 score
# 入参：数据库会话、归属用户、库编号、查询词和候选上限
# 返回：按相似度降序的候选行；Qdrant 或模型异常直接抛出
def _vector_search(
    db_session: Session, owner_id: int, knowledge_base_id: int, query: str, candidate_limit: int,
) -> list[dict]:
    hits = vector_index.search_vectors(
        owner_id, knowledge_base_id, vector_index.embed_query(query), candidate_limit,
    )
    rows_by_id = _chunk_rows_by_ids(db_session, owner_id, knowledge_base_id, [hit.chunk_id for hit in hits])
    candidates: list[dict] = []
    for hit in hits:
        row = rows_by_id.get(hit.chunk_id)
        if row is not None:
            candidates.append({**dict(row), "score": hit.score})
    return candidates


# 做什么：按最高相似度截断向量候选
# 入参：向量候选、目标条数和相对下限
# 返回：保留的命中；没有候选时为空
def _filter_vector(candidates: Sequence[dict], top_k: int, min_ratio: float) -> list[dict]:
    if not candidates:
        return []
    floor = candidates[0]["score"] * min_ratio
    return [candidate for candidate in candidates if candidate["score"] >= floor][:top_k]


# 做什么：归一化两路分数后合并，避免 BM25 和余弦绝对值直接相加
# 入参：关键词候选、向量候选、目标条数和相对下限
# 返回：按融合分降序去重后的命中；两路都空时为空
def _fuse_candidates(
    keyword_rows: Sequence[RowMapping], vector_rows: Sequence[dict], top_k: int, min_ratio: float,
) -> list[dict]:
    keyword_top = keyword_rows[0]["score"] if keyword_rows else 0.0
    vector_top = vector_rows[0]["score"] if vector_rows else 0.0
    fused_score: dict[int, float] = {}
    rows_by_id: dict[int, dict] = {}
    for row in keyword_rows:
        chunk_id = row["chunk_id"]
        rows_by_id[chunk_id] = dict(row)
        if keyword_top > 0:
            fused_score[chunk_id] = fused_score.get(chunk_id, 0.0) + float(row["score"]) / keyword_top
    for row in vector_rows:
        chunk_id = row["chunk_id"]
        rows_by_id[chunk_id] = dict(row)
        if vector_top > 0:
            fused_score[chunk_id] = fused_score.get(chunk_id, 0.0) + float(row["score"]) / vector_top
    ordered_ids = sorted(fused_score, key=lambda chunk_id: (-fused_score[chunk_id], chunk_id))
    if not ordered_ids:
        return []
    best = fused_score[ordered_ids[0]]
    kept_ids = [chunk_id for chunk_id in ordered_ids if fused_score[chunk_id] >= best * min_ratio]
    return [{**rows_by_id[chunk_id], "score": fused_score[chunk_id]} for chunk_id in kept_ids[:top_k]]


# 做什么：判断最高原始余弦是否低于回答下限，拦下时记录两路候选统计
# 入参：模式、知识库编号、下限、关键词候选数和降序向量候选
# 返回：有分数且低于下限时为 True；没有向量候选时为 False
def _below_answer_floor(
    mode: str, knowledge_base_id: int, floor: float, keyword_count: int, vector_rows: Sequence[dict],
) -> bool:
    # 新手注意：原始余弦像统一刻度的尺子，各路除以第一名后的分数只能比本次名次。
    if not vector_rows:
        return False
    highest_score = vector_rows[0]["score"]
    if highest_score >= floor:
        return False
    logger.info(
        "知识库检索拦截 | 模式=%s | knowledge_base_id=%s | floor=%.4f | 最高余弦=%.4f | "
        "关键词候选=%s | 向量候选=%s | 关键词有候选=%s",
        mode, knowledge_base_id, floor, highest_score, keyword_count, len(vector_rows), keyword_count > 0,
    )
    return True


# 做什么：识别向量服务的连接故障和服务端错误
# 入参：Qdrant 客户端异常
# 返回：服务不可用时为 True，协议解析和请求错误为 False
def _vector_service_unavailable(error: ResponseHandlingException | UnexpectedResponse) -> bool:
    if isinstance(error, ResponseHandlingException):
        return isinstance(error.source, TransportError)
    return error.status_code is not None and error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR


# 做什么：按配置选择关键词、向量或混合检索，并统一成候选/保留统计
# 入参：数据库会话、归属用户、库编号、查询词和结果上限
# 返回：命中及统计；向量服务不可用时走关键词，其他异常直接抛出
def search_relevant_chunks(db_session: Session, owner_id: int, knowledge_base_id: int,
                           query: str, top_k: int) -> RelevantSearchResult:
    settings = get_settings()
    mode = settings.vector_retrieval_mode if settings.vector_enabled else "keyword"
    candidate_limit = _candidate_limit(top_k)
    if mode == "keyword":
        return _keyword_search(db_session, owner_id, knowledge_base_id, query, top_k, candidate_limit)
    try:
        vector_rows = _vector_search(db_session, owner_id, knowledge_base_id, query, candidate_limit)
    except (ResponseHandlingException, UnexpectedResponse) as error:
        if not _vector_service_unavailable(error):
            raise
        logger.warning("知识库向量降级 | 模式=%s | knowledge_base_id=%s | error_type=%s",
                       mode, knowledge_base_id, type(error).__name__)
        return _keyword_search(db_session, owner_id, knowledge_base_id, query, top_k, candidate_limit, vector_unavailable=True)
    keyword_rows: Sequence[RowMapping] = ()
    if mode == "hybrid":
        keyword_rows = search_chunks(db_session, owner_id, knowledge_base_id, query, top_k, candidate_limit=candidate_limit)
    candidate_count = len({row["chunk_id"] for row in keyword_rows} | {row["chunk_id"] for row in vector_rows})
    if _below_answer_floor(mode, knowledge_base_id, settings.vector_answer_floor, len(keyword_rows), vector_rows):
        return RelevantSearchResult(hits=[], candidates=candidate_count, dropped=candidate_count)
    if mode == "vector":
        hits = _filter_vector(vector_rows, top_k, settings.vector_min_score_ratio)
        logger.info("知识库检索过滤 | 模式=vector | knowledge_base_id=%s | 候选=%s | 保留=%s | 丢弃=%s",
                    knowledge_base_id, candidate_count, len(hits), candidate_count - len(hits))
    else:
        hits = _fuse_candidates(keyword_rows, vector_rows, top_k, settings.search_min_score_ratio)
        logger.info("知识库检索过滤 | 模式=hybrid | knowledge_base_id=%s | 关键词候选=%s | 向量候选=%s | 合并候选=%s | 保留=%s | 丢弃=%s",
                    knowledge_base_id, len(keyword_rows), len(vector_rows), candidate_count, len(hits), candidate_count - len(hits))
    return RelevantSearchResult(hits=hits, candidates=candidate_count, dropped=candidate_count - len(hits))
