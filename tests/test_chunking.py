"""独立验证文本切分，测试不建数据库连接。"""
import pytest

from app.core.knowledge import CHUNK_HARD_MAX, CHUNK_MIN_CHARS, CHUNK_TARGET_MAX
from app.rag.chunking import ChunkDraft, split_document

SENTENCE = "资料" * 24 + "好。"
LONG_PARAGRAPH = SENTENCE * 40


# 做什么：确认短文本原样成为一块，包括前后空白
# 入参：无，使用固定标题和短文本
# 返回：无；切片或原文偏移不符合预期时断言失败
def test_short_document_stays_whole() -> None:
    content = "  短文本。\t"
    assert split_document("标题", content) == [
        ChunkDraft(0, "标题 > 第1段", content, 0, len(content)),
    ]


# 做什么：确认长段落按句切成多块，长度落在目标范围内
# 入参：无，使用 2000 字且长句间隔固定的文本
# 返回：无；切片数量或长度错误时断言失败
def test_long_paragraph_stays_within_target_sizes() -> None:
    chunks = split_document("标题", LONG_PARAGRAPH)
    assert len(chunks) > 1
    assert all(CHUNK_MIN_CHARS <= len(chunk.content) <= CHUNK_TARGET_MAX for chunk in chunks)
    assert all(chunk.content.endswith("。") for chunk in chunks)


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


# 做什么：确认空行分段和章节路径，段内单个 CRLF 保留
# 入参：无，使用包含 Windows 换行、空白行和两个短段的原文
# 返回：无；出现错误段号或正文变化时断言失败
def test_blank_lines_define_heading_paths() -> None:
    chunks = split_document("标题", "第一行\r\n第二行\r\n \t\r\n另一个段落")
    assert [chunk.heading_path for chunk in chunks] == ["标题 > 第1段", "标题 > 第2段"]
    assert [chunk.content for chunk in chunks] == ["第一行\r\n第二行", "另一个段落"]


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


# 做什么：检查高度不均匀的句长也能在目标范围内收尾
# 入参：一段带少量句末的 1151 字原文
# 返回：无；强行留下短尾块或超出目标长度时断言失败
def test_rebalances_chunks_to_avoid_short_tail() -> None:
    content = "甲" * 549 + "。" + "乙" * 599 + "。丙"
    chunks = split_document("标题", content)
    assert len(chunks) == 3
    assert all(CHUNK_MIN_CHARS <= len(chunk.content) <= CHUNK_TARGET_MAX for chunk in chunks)


# 做什么：检查无法同时满足两块最小长度和重叠时保留原段
# 入参：切分目标上限与双块最小长度之间的边界长度
# 返回：无；段落被拆成过短块或内容变化时断言失败
@pytest.mark.parametrize("length", [601, 719])
def test_paragraph_stays_whole_when_two_valid_chunks_cannot_fit(length: int) -> None:
    content = ("字。" * length)[:length]
    assert split_document("标题", content) == [ChunkDraft(0, "标题 > 第1段", content, 0, length)]


# 做什么：检查空内容或全空白时返回空列表
# 入参：空字符串或含空白行的原文
# 返回：无；产生空白切片或抛出异常时测试失败
@pytest.mark.parametrize("content", ["", " \t", "\r\n \t\n　"])
def test_blank_document_returns_no_chunks(content: str) -> None:
    assert split_document("标题", content) == []
