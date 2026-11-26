"""按完整请求预算选择最近连续的完整轮次，不调用模型做摘要。"""
from app.llm.tokens import effective_input_budget, ensure_request_budget, estimate_message_tokens, estimate_request_tokens
from app.rag.context import ReferenceBundle, compose_question
from app.schemas.chat import ContextCandidates, ContextMessage, ContextPlan


# 做什么：从最近轮次向前选择可容纳的完整问答，超限即停止
# 入参：已校验候选及扣除系统提示词和当前问题后的历史预算
# 返回：按时间正序排列的历史；最近一轮不适配时返回空列表
def select_history(messages: list[ContextMessage], remaining_tokens: int) -> list[ContextMessage]:
    if len(messages) % 2:
        raise ValueError("上下文必须由完整轮次组成")
    selected_messages = []
    for position in range(len(messages) - 2, -1, -2):
        question, answer = messages[position:position + 2]
        if question.role != "user" or answer.role != "assistant":
            raise ValueError("上下文角色顺序不合法")
        pair_tokens = estimate_message_tokens(question.content) + estimate_message_tokens(answer.content)
        if pair_tokens > remaining_tokens:
            break
        selected_messages[0:0] = [question, answer]
        remaining_tokens -= pair_tokens
    return selected_messages


# 做什么：生成包含裁剪标记的请求计划，完整保留系统提示词和当前问题
# 入参：有界历史候选、系统提示词、当前问题及可选检索结果
# 返回：最终历史及预算统计；基本输入不适配时抛安全异常
def plan_context(
    candidates: ContextCandidates, system_prompt: str, question: str, references: ReferenceBundle | None = None,
) -> ContextPlan:
    if references is not None:
        question = compose_question(references.block, question, reason=references.reason)
    base_tokens = ensure_request_budget(system_prompt, [], question)
    input_budget = effective_input_budget()
    messages = select_history(candidates.messages, input_budget - base_tokens)
    return ContextPlan(
        messages=messages, input_token_budget=input_budget,
        estimated_input_tokens=estimate_request_tokens(system_prompt, messages, question),
        history_truncated=candidates.history_truncated or len(messages) < len(candidates.messages),
        latest_round_omitted=candidates.latest_round_omitted or (bool(candidates.messages) and not messages),
    )


# 做什么：生成面向用户的历史裁剪提示，不把预算算法放进产品文案
# 入参：本次请求计划
# 返回：可用于 SSE 的安全提示和选入轮数，不包含历史正文
def context_notice(plan: ContextPlan) -> dict:
    retained_rounds = len(plan.messages) // 2
    message = f"本次仅携带最近 {retained_rounds} 轮成功对话，较早内容未参与回答；完整记录仍保存在历史中。"
    if plan.latest_round_omitted:
        message = "最近一轮内容过长或超出本次预算，本次未携带历史。请在问题中补充需要引用的关键信息；完整记录仍保留。"
    return {"message": message, "retained_rounds": retained_rounds, "latest_round_omitted": plan.latest_round_omitted}
