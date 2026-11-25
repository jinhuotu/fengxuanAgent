"""按同一次检索的最高分过滤全文候选。"""
from collections.abc import Mapping, Sequence
from typing import Any


# 做什么：按本次最高分和绝对下限筛选，再按原顺序取前 top_k 条
# 入参：已排序候选、返回上限、相对比例和绝对分数下限
# 返回：新的命中列表；无候选、上限非正或最高分非正时返回空列表
def select_relevant_hits[Hit: Mapping[str, Any]](
    rows: Sequence[Hit], top_k: int, min_score_ratio: float, min_score_absolute: float,
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
    return relevant_hits[:top_k]
