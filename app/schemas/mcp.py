"""MCP Server 配置 API 的请求/响应模型。"""

from typing import Any

from pydantic import BaseModel, Field


class McpServerCreateRequest(BaseModel):
    """创建 MCP Server（stdio）配置。"""

    server_id: str = Field(..., min_length=1, max_length=128, description="逻辑标识，同一用户下唯一")
    name: str = Field(..., min_length=1, max_length=255)
    command: str = Field(..., min_length=1, max_length=512, description="可执行文件，如 python、node、uv")
    args_json: list[str] = Field(default_factory=list, description="命令行参数列表")
    env_json: dict[str, Any] = Field(default_factory=dict, description="子进程环境变量")
    working_dir: str | None = Field(default=None, max_length=1024)
    enabled: bool = True
    startup_timeout_seconds: int = Field(default=30, ge=5, le=600)
    restart_policy: str = Field(default="restart", max_length=32)


class McpServerUpdateRequest(BaseModel):
    """更新 MCP Server 配置；仅提交需要修改的字段。"""

    server_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    command: str | None = Field(default=None, min_length=1, max_length=512)
    args_json: list[str] | None = None
    env_json: dict[str, Any] | None = None
    working_dir: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    startup_timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    restart_policy: str | None = Field(default=None, max_length=32)
