"""对话接口和服务之间交换的明确结构。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.chat import CHAT_DEFAULT_PAGE_SIZE, CHAT_INPUT_LENGTH, CHAT_MAX_PAGE_SIZE


class ChatPageQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=CHAT_DEFAULT_PAGE_SIZE, ge=1, le=CHAT_MAX_PAGE_SIZE)


class ChatSendRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    request_id: UUID
    content: str = Field(min_length=1, max_length=CHAT_INPUT_LENGTH)
    retry_of_request_id: UUID | None = None


class ChatSessionKnowledgeBaseUpdate(BaseModel):
    knowledge_base_id: int | None


class ChatSessionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    history_version: int
    active_request_id: str | None
    generation_expires_at: datetime | None
    knowledge_base_id: int | None = None


class ChatMessageReference(BaseModel):
    index: int
    chunk_id: int
    document_id: int
    heading_path: str
    char_start: int
    char_end: int
    score: float = Field(allow_inf_nan=False)


class ChatMessageItem(BaseModel):
    id: int
    request_id: str
    role: Literal["user", "assistant"]
    content: str
    status: Literal["generating", "completed", "failed", "interrupted"]
    model: str | None
    finish_reason: str | None
    error_code: str | None
    created_at: datetime
    references: list[ChatMessageReference] = Field(default_factory=list)


class PreparedTurn(BaseModel):
    user_id: int
    session_id: int
    request_id: str
    assistant_id: int
    history_version: int
    content: str
    knowledge_base_id: int | None = None


class ChatRequestResult(BaseModel):
    request_id: str
    question: ChatMessageItem
    answer: ChatMessageItem
    session_busy: bool
    can_retry: bool
    history_version: int
    active_request_id: str | None


class ContextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ContextCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    messages: list[ContextMessage]
    history_truncated: bool
    latest_round_omitted: bool


class ContextPlan(BaseModel):
    messages: list[ContextMessage]
    history_truncated: bool
    latest_round_omitted: bool
    estimated_input_tokens: int
    input_token_budget: int
    reference_block: str = ""
    reference_bytes: int = 0


class TurnResult(BaseModel):
    content: str = ""
    status: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    references: list[dict] = Field(default_factory=list)

