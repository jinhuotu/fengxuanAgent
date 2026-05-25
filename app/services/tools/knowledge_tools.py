"""
内置知识库工具（LangChain 结构化工具调用）。

阶段一要求：
- 使用 `@tool` 装饰器定义工具
- 通过 Pydantic `args_schema` 约束参数
- 输出为纯文本或可序列化字典（不含复杂对象）

说明：通过闭包绑定 Chroma 集合，工具侧仅接收 LLM 传入的 query，
知识库/集合由后端在构建时注入。
"""

from __future__ import annotations

from typing import Callable

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.integrations.vector_store import retrieve


class RetrieveContextArgs(BaseModel):
    """知识库检索工具参数。"""

    query: str = Field(..., description="要在知识库中检索的查询内容/问题。")


def build_retrieve_context_tool(chroma_collection: str) -> Callable[[str], str]:
    """
    构建绑定指定 Chroma 集合的 `retrieve_context` 工具。

    Args:
        chroma_collection: 要检索的 Chroma 集合名。

    Returns:
        接受 `query` 并返回上下文文本的 LangChain 工具函数。
    """

    @tool("retrieve_context", args_schema=RetrieveContextArgs)
    def retrieve_context(query: str) -> str:
        """
        根据查询从知识库检索相关上下文。

        当用户询问已存入知识库的信息时使用；返回可供 LLM 直接使用的纯文本。
        """

        texts = retrieve(chroma_collection, query, top_k=4)
        # 保持纯文本输出，便于下游解析。
        return "\n".join(texts)

    return retrieve_context

