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
# 剥词表后不超过这个长度就直接当探针：说明问句本身够短，探针就是原词。
PROBE_DIRECT_MAX_CHARS = 6
# 长句从长到短试片段，越长的片段越接近实体名，也越不容易撞上通用词。
PROBE_FRAGMENT_LENGTHS = (5, 4, 3, 2)
# 整段都是疑问用语的片段不拿来当探针，避免把问法当成关键词。
PROBE_STOP_FRAGMENTS = frozenset({
    "如何", "怎么", "怎样", "什么", "哪些", "哪个", "是否", "能否", "可以", "应该",
    "需要", "请问", "为什么", "多少", "一下", "帮我", "我们", "这个", "那个",
    "如何同步", "怎么同步", "有什么区别",
})
PROBE_SPLIT_CHARACTERS = PROBE_EDGE_CHARACTERS


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


# 做什么：把长问句按标点切成小段，供片段匹配使用
# 入参：原始问句
# 返回：去掉首尾标点后的小段列表；全空时返回空列表
def _query_segments(query: str) -> list[str]:
    segments: list[str] = []
    current = ""
    for character in query.strip(PROBE_EDGE_CHARACTERS):
        # 新手注意：片段不能跨标点，否则拼出来的词在正文里根本不会出现。
        if character in PROBE_SPLIT_CHARACTERS:
            if current:
                segments.append(current)
            current = ""
        else:
            current += character
    if current:
        segments.append(current)
    return segments


# 做什么：判断片段能不能当探针：不能是纯虚词，也不能带首尾符号
# 入参：待检查的片段
# 返回：可用返回 True；纯疑问用语或以符号开头结尾时返回 False
def _usable_fragment(fragment: str) -> bool:
    if len(fragment) < SEARCH_QUERY_MIN_CHARS or fragment in PROBE_STOP_FRAGMENTS:
        return False
    return fragment[0].isalnum() and fragment[-1].isalnum()


# 做什么：在候选正文里找问句中出现过、且能区分候选的片段
# 入参：原始问句和候选正文列表
# 返回：覆盖候选最多、同样覆盖时更长的片段；没有可区分片段时返回空串
def find_content_fragment(query: str, contents: Sequence[str]) -> str:
    segments = _query_segments(query)
    if not segments or not contents:
        return ""
    row_count = len(contents)
    best_fragment = ""
    best_weight = 0
    for segment in segments:
        for length in PROBE_FRAGMENT_LENGTHS:
            for start in range(len(segment) - length + 1):
                fragment = segment[start:start + length]
                if not _usable_fragment(fragment):
                    continue
                matched_rows = sum(1 for content in contents if fragment in content)
                # 命中所有候选的片段没有区分度，排不动顺序，直接跳过。
                if matched_rows == 0 or matched_rows == row_count:
                    continue
                # 新手注意：覆盖越广说明越接近问题主题，长度越大说明越具体，两者相乘压住只命中一块的噪声片段。
                weight = matched_rows * length
                if weight > best_weight or (weight == best_weight and len(fragment) > len(best_fragment)):
                    best_fragment, best_weight = fragment, weight
    return best_fragment


# 做什么：为本次检索挑选探针词：短问句用原词，长问句改从正文里挑片段
# 入参：用户原始问句和本次候选正文列表
# 返回：可用的探针词；两者都不成立时返回空串，检索退回纯分数排序
def choose_probe(query: str, contents: Sequence[str]) -> str:
    direct = extract_probe_terms(query)
    if direct and len(direct) <= PROBE_DIRECT_MAX_CHARS:
        return direct
    return find_content_fragment(query, contents)


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
