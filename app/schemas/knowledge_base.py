"""知识库请求/响应模型。"""

from pydantic import BaseModel


class KBCreateRequest(BaseModel):
    name: str
    description: str | None = None


class KBUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class KBIngestRequest(BaseModel):
    source_name: str
    content: str
