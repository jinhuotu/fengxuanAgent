"""内置 LangChain 工具（非 MCP）的目录与构建。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.knowledge_base import KnowledgeBase
from app.services.tools.knowledge_tools import build_retrieve_context_tool
from app.services.tools.runtime_tools import build_get_python_runtime_tool
from app.services.tools.system_tools import build_get_system_status_tool
from app.services.tools.time_tools import build_get_current_time_tool
from app.services.tools.bazi_tools import (
    build_compute_bazi_chart_tool,
    build_compute_luohou_tool,
    build_match_shengxiao_tool,
)
from app.services.tools.bazi_runner import resolve_bazi_home


@dataclass(frozen=True)
class BuiltinToolSpec:
    """单个内置工具的静态元数据。"""

    tool_key: str
    display_name: str
    description: str
    requires_kb: bool


# 内置工具清单与 UI 展示信息的唯一数据源。
BUILTIN_TOOL_SPECS: list[BuiltinToolSpec] = [
    BuiltinToolSpec(
        tool_key="get_system_status",
        display_name="系统状态",
        description="查询后端 MySQL、Chroma 等组件运行状态，用于排障或健康检查。",
        requires_kb=False,
    ),
    BuiltinToolSpec(
        tool_key="retrieve_context",
        display_name="知识库检索",
        description="按查询语句从当前会话绑定的知识库向量集合中检索相关文本片段。",
        requires_kb=True,
    ),
    BuiltinToolSpec(
        tool_key="get_current_time",
        display_name="当前时间",
        description="查询调用瞬间的服务器时间（UTC 与本地或指定 IANA 时区），用于「现在几点」「今天几号」等问题。",
        requires_kb=False,
    ),
    BuiltinToolSpec(
        tool_key="get_python_runtime",
        display_name="Python 运行时",
        description="返回后端进程使用的 Python 版本与实现名称，用于「用的 Python 几」「解释器版本」等问题。",
        requires_kb=False,
    ),
    BuiltinToolSpec(
        tool_key="compute_bazi_chart",
        display_name="八字排盘",
        description="根据出生年月日时排八字（需配置 BAZI_HOME 指向 china-testing/bazi 仓库）。",
        requires_kb=False,
    ),
    BuiltinToolSpec(
        tool_key="match_shengxiao",
        display_name="生肖合婚",
        description="按生肖查询三合、六合、冲刑害破等参考（需 BAZI_HOME）。",
        requires_kb=False,
    ),
    BuiltinToolSpec(
        tool_key="compute_luohou",
        display_name="罗喉日",
        description="计算罗喉日与杀师时（需 BAZI_HOME，luohou 依赖 sxtwl）。",
        requires_kb=False,
    ),
]

ALLOWED_TOOL_KEYS: frozenset[str] = frozenset(s.tool_key for s in BUILTIN_TOOL_SPECS)


def list_builtin_specs() -> list[BuiltinToolSpec]:
    return list(BUILTIN_TOOL_SPECS)


def build_builtin_langchain_tools(
    db: Session,
    kb: KnowledgeBase | None,
    enabled_by_key: dict[str, bool],
) -> list[Any]:
    """
    按用户 `enabled_by_key` 构建内置 LangChain 工具列表。

    未出现的 key 默认为 True（调用方应已合并数据库偏好）。
    """
    out: list[Any] = []
    for spec in BUILTIN_TOOL_SPECS:
        if not enabled_by_key.get(spec.tool_key, True):
            continue
        if spec.tool_key == "get_system_status":
            out.append(build_get_system_status_tool(db))
        elif spec.tool_key == "retrieve_context":
            if kb is not None:
                out.append(build_retrieve_context_tool(kb.chroma_collection))
        elif spec.tool_key == "get_current_time":
            out.append(build_get_current_time_tool())
        elif spec.tool_key == "get_python_runtime":
            out.append(build_get_python_runtime_tool())
        elif spec.tool_key == "compute_bazi_chart" and resolve_bazi_home() is not None:
            out.append(build_compute_bazi_chart_tool())
        elif spec.tool_key == "match_shengxiao" and resolve_bazi_home() is not None:
            out.append(build_match_shengxiao_tool())
        elif spec.tool_key == "compute_luohou" and resolve_bazi_home() is not None:
            out.append(build_compute_luohou_tool())
    return out
