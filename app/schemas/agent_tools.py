"""Agent 工具扩展 API 的请求/响应模型。"""

from pydantic import BaseModel, Field


class BuiltinToolPrefUpdateRequest(BaseModel):
    """切换当前用户对某内置工具的启用状态。"""

    enabled: bool = Field(..., description="是否挂载到对话工具列表")
