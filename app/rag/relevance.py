"""按同一次检索的最高分过滤全文候选。"""
from collections.abc import Mapping, Sequence
from string import whitespace
from typing import Any

from app.core.knowledge import SEARCH_QUERY_MIN_CHARS

PROBE_EDGE_CHARACTERS = whitespace + "\u3000\u00a0？！。，、：；\"'“”‘’（）?!.,:;()【】《》"
PROBE_PREFIXES = (
    "我想知道", "可不可以", "解释一下", "介绍一下", "说明一下",
    "请帮我", "能不能", "为什么", "什么是", "简单说",
    "请问", "帮我", "如何", "怎么", "怎样", "为何", "什么", "解释", "介绍", "说明",
)
PROBE_SUFFIXES = ("是什么意思", "有哪几类", "包括什么", "有什么用", "是什么", "有哪些", "吗", "呢")


# 做什么：从问句剥掉一层开头和结尾，留下用于匹配正文的探针词
# 入参：用户原始问句
# 返回：至少两字符的探针词；不足两字符时返回空串
def extract_probe_terms(query: str) -> str:
    probe = query.strip(PROBE_EDGE_CHARACTERS)
    # 新手注意：探针像书内查找用的关键词，先剥较长的问法才不会留下半句。
    for prefix in PROBE_PREFIXES:
        if probe.startswith(prefix):
            probe = probe[len(prefix):].strip(PROBE_EDGE_CHARACTERS)
            break
    for suffix in PROBE_SUFFIXES:
        if probe.endswith(suffix):
            probe = probe[:-len(suffix)].strip(PROBE_EDGE_CHARACTERS)
            break
    return probe if len(probe) >= SEARCH_QUERY_MIN_CHARS else ""


# 做什么：按本次最高分和绝对下限筛选，优先保留包含探针的结果顺序
# 入参：已排序候选、返回上限、相对比例、绝对分数下限和可选探针词
# 返回：新的命中列表；无候选、上限非正或最高分非正时返回空列表
def select_relevant_hits[Hit: Mapping[str, Any]](
    rows: Sequence[Hit], top_k: int, min_score_ratio: float, min_score_absolute: float,
    *, probe: str = "",
) -> list[Hit]:
    if not rows or top_k <= 0:
        return []
    candidate_scores = (row["score"] for row in rows)
    best_score = max(candidate_scores)
    if best_score <= 0:
        return []
    # 新手注意：相对阈值像按本场最高分划线，分数都小的场次也有机会保留命中。
    score_ratio = max(0.0, min(1.0, min_score_ratio))
    threshold = max(min_score_absolute, best_score * score_ratio)
    relevant_hits = [row for row in rows if row["score"] >= threshold]
    if probe:
        exact_hits = [row for row in relevant_hits if probe in row["content"]]
        other_hits = [row for row in relevant_hits if probe not in row["content"]]
        relevant_hits = exact_hits + other_hits
    return relevant_hits[:top_k]
