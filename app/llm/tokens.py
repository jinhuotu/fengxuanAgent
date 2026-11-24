"""无分词依赖的保守体积估算；不是供应商 tokenizer 的精确计数。"""
from app.core.chat_errors import ModelBudgetConfigurationError, ModelContextBudgetError
from app.core.config import get_settings
from app.schemas.chat import ContextMessage

MESSAGE_OVERHEAD_TOKENS = 16
REQUEST_OVERHEAD_TOKENS = 32
TOKEN_ESTIMATION_METHOD = "utf8_bytes_with_overhead"


# 做什么：按 UTF-8 字节数估算文本体积，偏保守而不承诺模型精确 token 数
# 入参：完整文本，不截断或改写
# 返回：字节数；空文本为零
def estimate_text_tokens(content: str) -> int:
    # 新手注意：这里像按包裹外形预留空间，并没有逐个数清模型内部的分词。
    return len(content.encode("utf-8"))


# 做什么：为一条消息预留文本和固定结构开销
# 入参：消息正文
# 返回：估算量；角色及分隔符开销按固定余量计入
def estimate_message_tokens(content: str) -> int:
    return estimate_text_tokens(content) + MESSAGE_OVERHEAD_TOKENS


# 做什么：计算完整请求输入体积，不只计算历史
# 入参：系统提示词、历史及当前问题
# 返回：包含消息结构的保守估算量
def estimate_request_tokens(system_prompt: str, history: list[ContextMessage], question: str) -> int:
    estimated_tokens = REQUEST_OVERHEAD_TOKENS
    estimated_tokens += estimate_message_tokens(system_prompt) + estimate_message_tokens(question)
    for message in history:
        estimated_tokens += estimate_message_tokens(message.content)
    return estimated_tokens


# 做什么：从兼容预算窗口中预留输出及安全空间，取得有效输入预算
# 入参：无，读取统一配置
# 返回：应用预算与剩余窗口的较小值；配置不可用时抛安全异常
def effective_input_budget() -> int:
    settings = get_settings()
    available_tokens = settings.llm_context_window_tokens - settings.llm_max_output_tokens - settings.chat_context_safety_tokens
    if available_tokens <= 0:
        raise ModelBudgetConfigurationError()
    return min(settings.chat_input_token_budget, available_tokens)


# 做什么：在实际发出模型请求前校验完整输入，禁止静默截断用户问题
# 入参：系统提示词、历史和当前问题
# 返回：估算输入量；超过有效预算时抛安全异常，不发送网络请求
def ensure_request_budget(system_prompt: str, history: list[ContextMessage], question: str) -> int:
    estimated_tokens = estimate_request_tokens(system_prompt, history, question)
    if estimated_tokens > effective_input_budget():
        raise ModelContextBudgetError()
    return estimated_tokens
