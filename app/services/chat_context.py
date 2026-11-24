"""按用户、会话和历史版本缓存有限的成功轮次。"""
import logging

from pydantic import ValidationError
from sqlalchemy import select

from app.core.chat import CHAT_CACHE_PREFIX, MessageRole, MessageStatus
from app.core.config import get_settings
from app.models.chat import ChatMessage
from app.schemas.chat import ContextCandidates, ContextMessage, ContextPlan, PreparedTurn
from app.services.chat_history import SessionFactory
from app.utils.cache import read_cache, write_cache
from app.llm import client as model_client
from app.llm.tokens import TOKEN_ESTIMATION_METHOD, estimate_text_tokens
from app.services.chat_budget import plan_context

logger = logging.getLogger(__name__)
CANDIDATE_CACHE_VERSION = "candidates-v2"
CONTEXT_LOOKAHEAD_ROUNDS = 1


# 做什么：筛出完整、按用户和助手交替排列且不超长的历史
# 入参：候选消息、最大轮数和最大字符数
# 返回：从最近轮次向前保留的完整上下文，单轮超限则停止向前读取
def bounded_context(messages: list[ContextMessage], max_rounds: int, max_characters: int) -> list[ContextMessage]:
    if len(messages) % 2:
        raise ValueError("上下文必须由完整轮次组成")
    result = []
    character_count = 0
    for position in range(len(messages) - 2, -1, -2):
        question, answer = messages[position:position + 2]
        validate_pair(question, answer)
        pair_length = len(question.content) + len(answer.content)
        if len(result) >= max_rounds * 2 or character_count + pair_length > max_characters:
            break
        result[0:0] = [question, answer]
        character_count += pair_length
    return result


# 做什么：拒绝角色错位的缓存，避免把用户输入误当成助手回答
# 入参：一轮用户问题和助手回答
# 返回：角色合法时正常结束；否则抛 ValueError 触发回源
def validate_pair(question: ContextMessage, answer: ContextMessage) -> None:
    if question.role != "user" or answer.role != "assistant":
        raise ValueError("上下文角色顺序不合法")


# 做什么：仅查询当前请求之前已完成的助手轮次和对应问题
# 入参：会话工厂、已通过归属校验的生成上下文和最大轮数
# 返回：顺序排列的完整成功历史，不包含失败和当前正在生成的消息
def query_context(sessions: SessionFactory, turn: PreparedTurn, max_rounds: int) -> list[ContextMessage]:
    with sessions() as db_session:
        request_query = select(ChatMessage.request_id).where(
            ChatMessage.session_id == turn.session_id, ChatMessage.id < turn.assistant_id,
            ChatMessage.role == MessageRole.ASSISTANT, ChatMessage.status == MessageStatus.COMPLETED,
            ChatMessage.is_deleted == 0,
        ).order_by(ChatMessage.id.desc()).limit(max_rounds)
        request_ids = list(db_session.scalars(request_query))
        if not request_ids:
            return []
        message_query = select(ChatMessage).where(
            ChatMessage.session_id == turn.session_id, ChatMessage.request_id.in_(request_ids),
            ChatMessage.is_deleted == 0,
        ).order_by(ChatMessage.id).limit(max_rounds * 2)
        messages = []
        for message in db_session.scalars(message_query):
            role_name = "user" if message.role == MessageRole.USER else "assistant"
            messages.append(ContextMessage(role=role_name, content=message.content))
    return messages


# 做什么：读取与当前问题无关的有界历史候选，保留裁剪原因
# 入参：会话工厂和已完成归属校验的生成上下文
# 返回：候选及裁剪标记；缓存失效回源，数据库错误直接抛出
def load_context_candidates(sessions: SessionFactory, turn: PreparedTurn) -> ContextCandidates:
    settings = get_settings()
    cache_key = (f"{CHAT_CACHE_PREFIX}:{CANDIDATE_CACHE_VERSION}:{turn.user_id}:{turn.session_id}:{turn.history_version}:"
                 f"{settings.chat_context_rounds}:{settings.chat_context_max_characters}")
    cached = read_cache(cache_key)
    if cached is not None:
        try:
            candidates = ContextCandidates.model_validate_json(cached)
            validate_candidates(candidates, settings.chat_context_rounds, settings.chat_context_max_characters)
            return candidates
        except (ValidationError, ValueError):
            logger.warning("上下文缓存格式损坏，重新查询 | session_id=%s", turn.session_id)
    messages = query_context(sessions, turn, settings.chat_context_rounds + CONTEXT_LOOKAHEAD_ROUNDS)
    selected = bounded_context(messages, settings.chat_context_rounds, settings.chat_context_max_characters)
    candidates = ContextCandidates(messages=selected, history_truncated=len(selected) < len(messages),
                                   latest_round_omitted=bool(messages) and not selected)
    write_cache(cache_key, candidates.model_dump_json(), settings.chat_cache_ttl_seconds)
    return candidates


# 做什么：拒绝超出缓存上限、角色错位或标记矛盾的候选缓存
# 入参：解析后的候选及当前轮数、字符限制
# 返回：合法时正常返回；损坏时抛 ValueError 触发回源
def validate_candidates(candidates: ContextCandidates, max_rounds: int, max_characters: int) -> None:
    bounded = bounded_context(candidates.messages, max_rounds, max_characters)
    if bounded != candidates.messages:
        raise ValueError("候选超过缓存限制")
    if candidates.latest_round_omitted and (candidates.messages or not candidates.history_truncated):
        raise ValueError("候选裁剪标记矛盾")


# 做什么：提供仅按轮数和字符限制的候选列表，兼容已有历史缓存调用
# 入参：会话工厂和生成上下文
# 返回：历史候选；真正模型调用还需根据当前问题计算请求预算
def load_context(sessions: SessionFactory, turn: PreparedTurn) -> list[ContextMessage]:
    return load_context_candidates(sessions, turn).messages


# 做什么：每次请求根据系统提示词和当前问题重新计算最终历史
# 入参：会话工厂、生成上下文和可选的当轮资料块
# 返回：完整请求计划及统计，基本输入超限时抛安全异常
def load_context_plan(sessions: SessionFactory, turn: PreparedTurn, reference_block: str = "") -> ContextPlan:
    candidates = load_context_candidates(sessions, turn)
    plan = plan_context(candidates, model_client.SYSTEM_PROMPT, turn.content, reference_block)
    if reference_block:
        plan.reference_block = reference_block
        plan.reference_bytes = estimate_text_tokens(reference_block)
    logger.info("上下文预算 | user_id=%s | session_id=%s | request_id=%s | model=%s | estimated_input_tokens=%s | "
                "input_budget=%s | retained_rounds=%s | history_truncated=%s | latest_round_omitted=%s | estimation_method=%s",
                turn.user_id, turn.session_id, turn.request_id, get_settings().llm_model, plan.estimated_input_tokens,
                plan.input_token_budget, len(plan.messages) // 2, plan.history_truncated, plan.latest_round_omitted,
                TOKEN_ESTIMATION_METHOD)
    return plan


# 做什么：成功保存后预热下一版本，使下一轮能够实际命中缓存
# 入参：会话工厂和刚刚完成的生成上下文
# 返回：无；只写对应旧快照的新版本键，不会覆盖更晚版本
def warm_context(sessions: SessionFactory, turn: PreparedTurn) -> None:
    if not get_settings().redis_enabled:
        return
    next_snapshot = turn.model_copy(update={
        "history_version": turn.history_version + 1, "assistant_id": turn.assistant_id + 1,
    })
    load_context(sessions, next_snapshot)

