"""
内置系统工具（LangChain 结构化工具调用）。

阶段一要求：
- 使用 `@tool` 装饰器定义工具
- 通过 Pydantic `args_schema` 约束参数
- 输出为纯文本或可序列化字典（不含复杂对象）
"""

from __future__ import annotations

from typing import Callable

from langchain_core.tools import tool
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.services.system_service import get_system_status as fetch_system_status


class EmptyArgs(BaseModel):
    """无入参工具的空调参模型。"""

    pass


def build_get_system_status_tool(db: Session) -> Callable[[], dict]:
    """
    构建绑定当前 DB 会话的 `get_system_status` 工具。

    Args:
        db: 后端请求上下文提供的 SQLAlchemy 会话。

    Returns:
        无输入参数的 LangChain 工具函数。
    """

    @tool("get_system_status", args_schema=EmptyArgs)
    def get_system_status() -> dict:
        """
        获取后端组件与已加载模型的运行状态。

        用于排障或确认 MySQL / Chroma 是否可用。
        """

        return fetch_system_status(db)

    return get_system_status

