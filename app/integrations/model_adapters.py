"""面向 API / Ollama / 远程端点的统一模型适配层。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama


def _openai_compatible_extra_body(base_url: str | None, model_params: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    合并用户 `model_params.extra_body` 与厂商特定默认值。

    DeepSeek（api.deepseek.com）在 thinking 默认开启时，若出现 tool call，后续轮次必须把
    上一轮 assistant 的 `reasoning_content` 原样带回；LangChain 构造消息时容易丢失该字段，
    导致 400: reasoning_content must be passed back。默认关闭 thinking；若用户在 model_params
    里显式传入 `extra_body.thinking` 则尊重用户配置。
    """
    mp = model_params or {}
    raw = mp.get("extra_body")
    extra: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    url = (base_url or "").lower()
    if "deepseek.com" in url and "thinking" not in extra:
        extra["thinking"] = {"type": "disabled"}
    return extra if extra else None


class ModelAdapter(ABC):
    """LangChain 聊天模型的薄封装。

    阶段一要求：
    - 通过 `bind_tools` 支持 LangChain 结构化工具调用。
    - 在需要时返回原始 `AIMessage`。
    - 保留现有 `invoke` / `stream` 接口以兼容旧调用方。
    """

    @abstractmethod
    def get_llm(self) -> Any:
        """返回底层 LangChain 聊天模型实例。"""

    @abstractmethod
    def invoke(self, prompt: str, system_prompt: str | None = None) -> str:
        raise NotImplementedError

    def bind_tools(self, tools: list[Any]) -> Any:
        """将结构化工具绑定到底层聊天模型（工具调用）。"""

        llm = self.get_llm()
        # 多数 LangChain 聊天模型实现了 `bind_tools`。
        if hasattr(llm, "bind_tools"):
            return llm.bind_tools(tools)
        return llm

    def invoke_ai_message(self, messages: Sequence[BaseMessage]) -> AIMessage:
        """调用底层聊天模型并返回原始 AIMessage。"""

        llm = self.get_llm()
        result = llm.invoke(list(messages))
        # LangChain 聊天模型的 `.invoke(...)` 通常返回 AIMessage。
        return result

    async def stream(self, prompt: str, system_prompt: str | None = None) -> AsyncGenerator[str, None]:
        result = self.invoke(prompt, system_prompt)
        yield result


class OpenAICompatibleAdapter(ModelAdapter):
    def __init__(
        self,
        model_name: str,
        base_url: str | None,
        api_key: str | None,
        temperature: float = 0.7,
        model_params: dict[str, Any] | None = None,
    ) -> None:
        llm_kwargs: dict[str, Any] = {
            "model": model_name,
            "base_url": base_url,
            "api_key": api_key,
            "temperature": temperature,
        }
        extra_body = _openai_compatible_extra_body(base_url, model_params)
        if extra_body is not None:
            llm_kwargs["extra_body"] = extra_body
        self.llm = ChatOpenAI(**llm_kwargs)

    def get_llm(self) -> Any:
        return self.llm

    def invoke(self, prompt: str, system_prompt: str | None = None) -> str:
        msgs = [HumanMessage(content=prompt)]
        if system_prompt:
            msgs.insert(0, SystemMessage(content=system_prompt))
        return self.llm.invoke(msgs).content


class OllamaAdapter(ModelAdapter):
    def __init__(self, model_name: str, base_url: str | None, temperature: float = 0.7) -> None:
        self.llm = ChatOllama(model=model_name, base_url=base_url or "http://127.0.0.1:11434", temperature=temperature)

    def get_llm(self) -> Any:
        return self.llm

    def invoke(self, prompt: str, system_prompt: str | None = None) -> str:
        msgs = [HumanMessage(content=prompt)]
        if system_prompt:
            msgs.insert(0, SystemMessage(content=system_prompt))
        return self.llm.invoke(msgs).content


class RemoteEndpointAdapter(OpenAICompatibleAdapter):
    """远程模型端点适配器；复用 OpenAI 兼容协议。"""
