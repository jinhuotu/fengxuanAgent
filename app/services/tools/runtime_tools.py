"""暴露进程/解释器信息的内置工具（无副作用）。"""

from __future__ import annotations

import sys
from typing import Any

from langchain_core.tools import tool


def build_get_python_runtime_tool() -> Any:
    """返回 LangChain 工具 `get_python_runtime`（版本与平台信息）。"""

    @tool("get_python_runtime")
    def get_python_runtime() -> dict:
        """
        返回当前后端进程使用的 Python 实现版本与平台标签。
        用于回答「用的 Python 几」「运行环境」等问题；不要用于安全敏感决策。
        """
        return {
            "version": sys.version.split()[0],
            "full_version": sys.version,
            "implementation": sys.implementation.name,
        }

    return get_python_runtime
