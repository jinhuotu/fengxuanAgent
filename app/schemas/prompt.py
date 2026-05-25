"""提示词模板请求/响应模型。"""

from pydantic import BaseModel


class PromptTemplateCreateRequest(BaseModel):
    name: str
    tags: str | None = None
    template: str


class PromptTemplateUpdateRequest(BaseModel):
    name: str | None = None
    tags: str | None = None
    template: str | None = None
