"""独立验证文本切分，测试不建数据库连接。"""
import pytest

from app.core.knowledge import CHUNK_HARD_MAX, CHUNK_MIN_CHARS, CHUNK_OVERLAP_MAX, CHUNK_OVERLAP_MIN, CHUNK_TARGET_MAX
from app.rag.chunking import ChunkDraft, split_document

SENTENCE = "资料" * 24 + "好。"
LONG_PARAGRAPH = SENTENCE * 40


# 做什么：逐块核对正文与左闭右开的原文偏移
# 入参：完整原文及切分得到的所有块
# 返回：无；越界、空区间或正文不一致时断言失败
def _assert_original_slices(content: str, chunks: list[ChunkDraft]) -> None:
    for chunk in chunks:
        assert 0 <= chunk.char_start < chunk.char_end <= len(content)
        assert chunk.content == content[chunk.char_start:chunk.char_end]


# 做什么：确认短文本原样成为一块，包括前后空白
# 入参：无，使用固定标题和短文本
# 返回：无；切片或原文偏移不符合预期时断言失败
def test_short_document_stays_whole() -> None:
    content = "  短文本。\t"
    chunks = split_document("标题", content)
    assert chunks == [
        ChunkDraft(0, "标题 > 第1段", content, 0, len(content)),
    ]
    _assert_original_slices(content, chunks)


# 做什么：确认长段落按句切成多块，长度落在目标范围内
# 入参：无，使用 2000 字且长句间隔固定的文本
# 返回：无；切片数量或长度错误时断言失败
def test_long_paragraph_stays_within_target_sizes() -> None:
    chunks = split_document("标题", LONG_PARAGRAPH)
    assert len(chunks) > 1
    assert all(CHUNK_MIN_CHARS <= len(chunk.content) <= CHUNK_TARGET_MAX for chunk in chunks)
    assert all(chunk.content.endswith("。") for chunk in chunks)
    _assert_original_slices(LONG_PARAGRAPH, chunks)


# 做什么：确认同一段相邻切片重叠 50 至 80 字，并从完整句子开始
# 入参：无，使用带规则句末的长段落
# 返回：无；重叠长度、文本或句末位置错误时断言失败
def test_adjacent_chunks_overlap_at_sentence_boundaries() -> None:
    chunks = split_document("标题", LONG_PARAGRAPH)
    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:]):
        overlap = previous.char_end - current.char_start
        assert 50 <= overlap <= 80
        assert previous.content[-overlap:] == current.content[:overlap]
        assert LONG_PARAGRAPH[current.char_start - 1] == "。"
    _assert_original_slices(LONG_PARAGRAPH, chunks)


# 做什么：确认短段合并时保留段内 CRLF 和段间空行
# 入参：无，使用包含 Windows 换行、空白行和两个短段的原文
# 返回：无；出现错误段号或正文变化时断言失败
def test_blank_lines_define_heading_paths() -> None:
    content = "第一行\r\n第二行\r\n \t\r\n另一个段落"
    chunks = split_document("标题", content)
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1-2段"]
    assert [chunk.content for chunk in chunks] == [content]
    _assert_original_slices(content, chunks)


# 做什么：逐块验证 Unicode 字符偏移可以直接切回准确原文
# 入参：无，使用短段和带编号、表情、换行的长段
# 返回：无；序号、切片正文或偏移不一致时断言失败
def test_all_offsets_match_slices_of_original_content() -> None:
    sentences = [f"第{number}句😀" + "说明" * 34 + "！\n" for number in range(30)]
    content = "  开头\r\n\r\n" + "".join(sentences) + " 尾声 "
    chunks = split_document("标题", content)
    assert len(chunks) > 3
    for chunk_index, chunk in enumerate(chunks):
        assert chunk.chunk_index == chunk_index
        assert 0 <= chunk.char_start < chunk.char_end <= len(content)
        assert content[chunk.char_start:chunk.char_end] == chunk.content


# 做什么：确认没有句末的超长句按字符硬切且段尾不丢
# 入参：无，使用 2301 字且没有标点的单个长句
# 返回：无；大小超限、重叠错位或覆盖不完整时断言失败
def test_unbroken_sentence_is_hard_split_with_complete_coverage() -> None:
    content = ("甲乙丙丁戊己庚辛壬癸" * 231)[:2301]
    chunks = split_document("标题", content)
    assert len(chunks) > 1
    assert all(CHUNK_MIN_CHARS <= len(chunk.content) <= CHUNK_HARD_MAX for chunk in chunks)
    stitched = chunks[0].content
    for previous, current in zip(chunks, chunks[1:]):
        overlap = previous.char_end - current.char_start
        assert 50 <= overlap <= 80
        stitched += current.content[overlap:]
    assert stitched == content
    _assert_original_slices(content, chunks)


# 做什么：检查高度不均匀的句长也能在目标范围内收尾
# 入参：一段带少量句末的 1151 字原文
# 返回：无；强行留下短尾块或超出目标长度时断言失败
def test_rebalances_chunks_to_avoid_short_tail() -> None:
    content = "甲" * 549 + "。" + "乙" * 599 + "。丙"
    chunks = split_document("标题", content)
    assert len(chunks) == 3
    assert all(CHUNK_MIN_CHARS <= len(chunk.content) <= CHUNK_TARGET_MAX for chunk in chunks)
    _assert_original_slices(content, chunks)


# 做什么：检查无法同时满足两块最小长度和重叠时保留原段
# 入参：切分目标上限与双块最小长度之间的边界长度
# 返回：无；段落被拆成过短块或内容变化时断言失败
@pytest.mark.parametrize("length", [601, 700, 719])
def test_paragraph_stays_whole_when_two_valid_chunks_cannot_fit(length: int) -> None:
    content = ("字。" * length)[:length]
    chunks = split_document("标题", content)
    assert chunks == [ChunkDraft(0, "标题 > 第1段", content, 0, length)]
    _assert_original_slices(content, chunks)


# 做什么：检查空内容或全空白时返回空列表
# 入参：空字符串或含空白行的原文
# 返回：无；产生空白切片或抛出异常时测试失败
@pytest.mark.parametrize("content", ["", " \t", "\r\n \t\n　", "   "])
def test_blank_document_returns_no_chunks(content: str) -> None:
    assert split_document("标题", content) == []


# 做什么：验证大量短段按顺序合并，只有末块可以不足 400 字
# 入参：二十个 45 字段落，由空行分隔
# 返回：无；块数、长度、标题或偏移不符合预期时断言失败
def test_twenty_short_paragraphs_merge_into_a_few_chunks() -> None:
    paragraphs = ["字" * 45] * 20
    content = "\n\n".join(paragraphs)
    chunks = split_document("标题", content)
    assert len(chunks) == 2
    assert all(len(chunk.content) >= CHUNK_MIN_CHARS for chunk in chunks[:-1])
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1-12段", "标题 > 第13-20段"]
    assert chunks[0].char_end < chunks[1].char_start
    assert chunks[-1].char_end == len(content)
    _assert_original_slices(content, chunks)


# 做什么：验证短段先合并，超过硬上限的单段保持独立切分
# 入参：两段短文本和一段超过硬上限的完整原文
# 返回：无；标题、组间边界或长段自身的切分发生变化时断言失败
def test_short_paragraphs_merge_without_changing_the_long_paragraph() -> None:
    short_group = "甲" * 180 + "\n\n" + "乙" * 240
    content = short_group + "\n\n" + LONG_PARAGRAPH
    chunks = split_document("标题", content)
    assert chunks[0] == ChunkDraft(0, "标题 > 第1-2段", short_group, 0, len(short_group))
    long_chunks = chunks[1:]
    assert len(long_chunks) > 1 and all(chunk.heading_path == "标题 > 第3段" for chunk in long_chunks)
    long_start = len(short_group) + 2
    assert long_chunks[0].char_start == long_start
    standalone = split_document("标题", LONG_PARAGRAPH)
    assert len(long_chunks) == len(standalone)
    for actual, expected in zip(long_chunks, standalone):
        assert actual.content == expected.content
        assert (actual.char_start, actual.char_end) == (long_start + expected.char_start, long_start + expected.char_end)
    for previous, current in zip(long_chunks, long_chunks[1:]):
        assert CHUNK_OVERLAP_MIN <= previous.char_end - current.char_start <= CHUNK_OVERLAP_MAX
    _assert_original_slices(content, chunks)


# 做什么：验证够下限后只补到目标长度，随后另开一组
# 入参：前两段恰好并到 600 字，第三段能并入硬上限但超出目标长度
# 返回：无；两个分组的精确边界、标题或正文有误时断言失败
def test_paragraph_group_closes_at_the_target_length() -> None:
    paragraphs = ["甲" * 400, "乙" * 198, "丙" * 100, "丁" * 300]
    content = "\n\n".join(paragraphs)
    chunks = split_document("标题", content)
    assert [(chunk.char_start, chunk.char_end) for chunk in chunks] == [(0, 600), (602, 1004)]
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1-2段", "标题 > 第3-4段"]
    assert all(chunk.char_end - chunk.char_start <= CHUNK_HARD_MAX for chunk in chunks)
    _assert_original_slices(content, chunks)


# 做什么：验证不足下限时可以合并到目标以上，并让组内所有块共享标题
# 入参：两短段合计 750 字，超过整块上限、低于组硬上限
# 返回：无；标题、组外边界或组内重叠不符时断言失败
def test_chunks_split_from_a_merged_group_share_the_heading_path() -> None:
    content = "甲" * 350 + "\n\n" + "乙" * 398
    chunks = split_document("标题", content)
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1-2段", "标题 > 第1-2段"]
    assert chunks[0].char_start == 0 and chunks[1].char_end == len(content) <= CHUNK_HARD_MAX
    overlap = chunks[0].char_end - chunks[1].char_start
    assert CHUNK_OVERLAP_MIN <= overlap <= CHUNK_OVERLAP_MAX
    assert all(len(chunk.content) >= CHUNK_MIN_CHARS for chunk in chunks)
    _assert_original_slices(content, chunks)


# 做什么：确认硬上限阻止合并时仍保留短组，包括不足 400 字的末组
# 入参：350 字与 700 字的两个段落，验证两种顺序
# 返回：无；短组丢失、越界合并或组间重叠时断言失败
@pytest.mark.parametrize(("first_length", "second_length"), [(350, 700), (700, 350)])
def test_keeps_short_group_when_the_hard_limit_prevents_merging(first_length: int, second_length: int) -> None:
    content = "甲" * first_length + "\n\n" + "乙" * second_length
    chunks = split_document("标题", content)
    assert [len(chunk.content) for chunk in chunks] == [first_length, second_length]
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1段", "标题 > 第2段"]
    assert chunks[0].char_end + 2 == chunks[1].char_start
    _assert_original_slices(content, chunks)
