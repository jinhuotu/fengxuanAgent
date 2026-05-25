"""内置工具：回显输入（学习示例，用于练通工具注册流程）。"""
from __future__ import annotations

from typing import Callable

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    """回显工具的入参。"""

    text: str = Field(description="需要原样返回的文本。")


def build_get_echo_tool() -> Callable[..., dict]:
    """返回 LangChain 工具 `get_echo`。"""

    @tool("get_echo", args_schema=EchoArgs)
    def get_echo(text: str) -> dict:
        """
        将输入文本原样返回。用于测试工具调用链路或演示简单内置工具。
        """
        return {"echo": text}

    return get_echo
