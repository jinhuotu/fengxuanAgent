"""模型配置请求/响应模型。"""

from typing import Any
from pydantic import BaseModel


class ModelCreateRequest(BaseModel):
    model_type: str
    model_name: str
    api_key: str | None = None
    base_url: str | None = None
    model_params: dict[str, Any] = {}


class ModelUpdateRequest(BaseModel):
    model_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_params: dict[str, Any] | None = None
