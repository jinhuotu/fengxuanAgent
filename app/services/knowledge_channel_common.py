"""各通道共用的块组装：把分好的块拼成原文并重算字符偏移。"""
from dataclasses import dataclass

from app.core.knowledge import CHUNK_HARD_MAX, CHUNK_HEADING_PATH_LENGTH
from app.rag.chunking import ChunkDraft, split_document

DRAFT_JOINER = "\n\n"
DEFAULT_HEADING = "正文"
PART_SUFFIX = "（第{}段）"


@dataclass(frozen=True)
class ParsedFile:
    """解析结果：可入库的原文，以及带偏移的块。"""

    text: str
    drafts: list[ChunkDraft]


# 做什么：把标题路径截到数据库允许的长度
# 入参：拼接好的标题路径
# 返回：不超过上限的标题；空标题退回"正文"
def clip_heading(heading: str) -> str:
    text = heading.strip() or DEFAULT_HEADING
    return text[:CHUNK_HEADING_PATH_LENGTH]


# 做什么：把一个语义单元切成符合长度上限的块
# 入参：块的标题路径和单元正文
# 返回：块列表；单元过长时交给通用切分器按段落二次切分
def make_draft(heading: str, text: str) -> list[ChunkDraft]:
    body = text.strip()
    if not body:
        return []
    if len(body) <= CHUNK_HARD_MAX:
        return [ChunkDraft(0, clip_heading(heading), body, 0, len(body))]
    pieces = split_document(clip_heading(heading), body)
    many = len(pieces) > 1
    return [
        ChunkDraft(
            piece.chunk_index,
            clip_heading(f"{heading}{PART_SUFFIX.format(piece.chunk_index + 1)}" if many else heading),
            piece.content,
            0,
            len(piece.content),
        )
        for piece in pieces
    ]


# 做什么：把多个块拼成完整原文，并按拼接位置重算字符偏移
# 入参：各通道产出的块（偏移可先填零）
# 返回：原文与偏移正确的块；引用跳转依赖这份偏移
def finalize_drafts(drafts: list[ChunkDraft]) -> ParsedFile:
    parts: list[str] = []
    rebuilt: list[ChunkDraft] = []
    cursor = 0
    for draft in drafts:
        body = draft.content.strip()
        if not body:
            continue
        if parts:
            cursor += len(DRAFT_JOINER)
        start = cursor
        cursor += len(body)
        rebuilt.append(ChunkDraft(len(rebuilt), draft.heading_path, body, start, cursor))
        parts.append(body)
    return ParsedFile(text=DRAFT_JOINER.join(parts), drafts=rebuilt)
