"""聊天相关请求/响应模型。"""

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    title: str = "新会话"
    model_config_id: int | None = None
    client_label: str | None = None


class ChatRequest(BaseModel):
    session_id: int
    model_id: int
    message: str
    stream: bool = False
    template_id: int | None = None
    kb_id: int | None = None
    output_mode: str = "text"


class ChatResponse(BaseModel):
    answer: str
    turn_count: int
    notice: str | None = None
    tools_used: list[str] = Field(default_factory=list)
