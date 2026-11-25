"""生成可按字符偏移回到原文的切片。"""
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
import re

from app.core.knowledge import (
    CHUNK_HARD_MAX,
    CHUNK_MIN_CHARS,
    CHUNK_OVERLAP_MAX,
    CHUNK_OVERLAP_MIN,
    CHUNK_TARGET_MAX,
)

# 原子分组让 Windows 的 CRLF 只算一次换行，避免把单行换行当成空行。
PARAGRAPH_BREAK = re.compile(r"(?>\r\n|[\r\n])(?:[^\S\r\n]*(?>\r\n|[\r\n]))+")
SENTENCE_END = re.compile(r"[。！？.!?]+|\r\n|[\r\n]")
MIN_SPLITTABLE_CHARS = 2 * CHUNK_MIN_CHARS - CHUNK_OVERLAP_MAX
WHOLE_CHUNK_MAX = min(CHUNK_HARD_MAX, MIN_SPLITTABLE_CHARS - 1)


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    heading_path: str
    content: str
    char_start: int
    char_end: int


# 做什么：按空行找出各个非空段落在原文中的范围
# 入参：完整原文，保留各种换行符
# 返回：按顺序产生段落起止偏移；纯空白不产生段落
def _paragraph_ranges(content: str) -> Iterator[tuple[int, int]]:
    paragraph_start = 0
    for separator in PARAGRAPH_BREAK.finditer(content):
        paragraph_end = separator.start()
        if content[paragraph_start:paragraph_end].strip():
            yield paragraph_start, paragraph_end
        paragraph_start = separator.end()
    if content[paragraph_start:].strip():
        yield paragraph_start, len(content)


# 做什么：按原文顺序合并相邻段落，只延伸字符区间
# 入参：完整原文，用现有空行规则识别非空段落
# 返回：每组的起止偏移和首末段号；空白原文不产生分组
def _paragraph_groups(content: str) -> Iterator[tuple[int, int, int, int]]:
    paragraphs = enumerate(_paragraph_ranges(content), start=1)
    first_paragraph = next(paragraphs, None)
    if first_paragraph is None:
        return

    first_number, (group_start, group_end) = first_paragraph
    last_number = first_number
    for paragraph_number, (paragraph_start, paragraph_end) in paragraphs:
        current_length = group_end - group_start
        merged_length = paragraph_end - group_start
        # 新手注意：分组像延长书签覆盖的范围，段间空行仍留在原文里。
        can_merge = merged_length <= CHUNK_HARD_MAX and (
            current_length < CHUNK_MIN_CHARS or merged_length <= CHUNK_TARGET_MAX
        )
        # 硬上限优先。即使当前组还短，也必须在会越界时收口。
        if not can_merge:
            yield group_start, group_end, first_number, last_number
            group_start = paragraph_start
            first_number = paragraph_number
        group_end = paragraph_end
        last_number = paragraph_number
    yield group_start, group_end, first_number, last_number


# 做什么：找到指定范围内最靠后的句末或换行后位置
# 入参：升序排列的句末偏移与两侧边界
# 返回：最近的句末偏移；没有可用句末时返回 None
def _last_sentence_end(sentence_ends: list[int], earliest: int, latest: int) -> int | None:
    # 新手注意：二分查找像按页码翻书，从中间排除不可能的半边。
    boundary_index = bisect_right(sentence_ends, latest) - 1
    if boundary_index >= 0 and sentence_ends[boundary_index] >= earliest:
        return sentence_ends[boundary_index]
    return None


# 做什么：计算下一块起点的可用范围，给段尾保留足够字符
# 入参：当前块起点与段落终点，调用方已确认段落还可继续切
# 返回：允许的最早和最晚起点；合法范围内总能保留规定的重叠长度
def _next_start_window(chunk_start: int, paragraph_end: int) -> tuple[int, int]:
    earliest = chunk_start + CHUNK_MIN_CHARS - CHUNK_OVERLAP_MAX
    latest = min(chunk_start + CHUNK_TARGET_MAX - CHUNK_OVERLAP_MIN, paragraph_end - CHUNK_MIN_CHARS)
    # 能用一块收尾就保留 400 到 600 字，否则留足两块可分的长度。
    final_chunk_start = paragraph_end - CHUNK_TARGET_MAX
    if final_chunk_start <= latest:
        return max(earliest, final_chunk_start), latest
    return earliest, min(latest, paragraph_end - MIN_SPLITTABLE_CHARS)


# 做什么：选当前块终点和下一块起点，尽量让两处都落在句末后
# 入参：块起点、段落终点和该段的句末偏移
# 返回：当前块终点及重叠后的下一块起点；句末不合用时按字符偏移切
def _split_points(chunk_start: int, paragraph_end: int, sentence_ends: list[int]) -> tuple[int, int]:
    earliest_start, latest_start = _next_start_window(chunk_start, paragraph_end)
    earliest_end = max(chunk_start + CHUNK_MIN_CHARS, earliest_start + CHUNK_OVERLAP_MIN)
    latest_end = min(chunk_start + CHUNK_TARGET_MAX, latest_start + CHUNK_OVERLAP_MAX)
    first_end_index = bisect_left(sentence_ends, earliest_end)
    last_end_index = bisect_right(sentence_ends, latest_end) - 1
    for end_index in range(last_end_index, first_end_index - 1, -1):
        chunk_end = sentence_ends[end_index]
        next_start = _last_sentence_end(
            sentence_ends, max(earliest_start, chunk_end - CHUNK_OVERLAP_MAX),
            min(latest_start, chunk_end - CHUNK_OVERLAP_MIN),
        )
        if next_start is not None:
            return chunk_end, next_start

    # 两处不能同时落在句末时，优先让下一块从完整句子开始。
    next_start = _last_sentence_end(sentence_ends, earliest_start, latest_start)
    if next_start is not None:
        return min(latest_end, next_start + CHUNK_OVERLAP_MAX), next_start
    chunk_end = _last_sentence_end(sentence_ends, earliest_end, latest_end)
    if chunk_end is None:
        chunk_end = latest_end
    return chunk_end, max(earliest_start, chunk_end - CHUNK_OVERLAP_MAX)


# 做什么：把指定的原文范围切成一块或若干有重叠的块
# 入参：完整原文与当前分组的起止偏移
# 返回：依原文顺序产生块的起止偏移
def _chunk_ranges(content: str, paragraph_start: int, paragraph_end: int) -> Iterator[tuple[int, int]]:
    # 601 到 719 字无法拆成两个至少 400 字且最多重叠 80 字的块。
    if paragraph_end - paragraph_start <= WHOLE_CHUNK_MAX:
        yield paragraph_start, paragraph_end
        return
    sentence_matches = SENTENCE_END.finditer(content, paragraph_start, paragraph_end)
    sentence_ends = [sentence_end.end() for sentence_end in sentence_matches]
    chunk_start = paragraph_start
    while paragraph_end - chunk_start > WHOLE_CHUNK_MAX:
        chunk_end, next_start = _split_points(chunk_start, paragraph_end, sentence_ends)
        yield chunk_start, chunk_end
        chunk_start = next_start
    yield chunk_start, paragraph_end


# 做什么：合并相邻短段，再按区间生成带顺序和段落标题的切片
# 入参：文档标题和完整原文
# 返回：从零编号的切片列表；空内容或纯空白返回空列表
def split_document(title: str, content: str) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for group_start, group_end, first_number, last_number in _paragraph_groups(content):
        paragraph_label = str(first_number) if first_number == last_number else f"{first_number}-{last_number}"
        heading_path = f"{title} > 第{paragraph_label}段"
        for char_start, char_end in _chunk_ranges(content, group_start, group_end):
            # 新手注意：偏移像书签，左端包含、右端不包含，打开原文就能还原这块文字。
            chunks.append(ChunkDraft(len(chunks), heading_path, content[char_start:char_end], char_start, char_end))
    return chunks
