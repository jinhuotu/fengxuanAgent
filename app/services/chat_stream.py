"""流式生成编排；断线、异常和成功都必须结束数据库中的生成状态。"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import dataclass
from time import monotonic

from anyio import CancelScope
from starlette.concurrency import run_in_threadpool

from app.core.chat import CHAT_STREAM_HEARTBEAT_SECONDS, MessageStatus
from app.core.chat_errors import MODEL_EVENT_CODES, ModelUnavailableError, ModelTimeoutError, ModelProtocolError
from app.core.config import get_settings
from app.llm.client import ModelChunk, SUPPORTED_FINISH_REASONS, stream_model
from app.rag.context import ReferenceBundle, collect_references, compose_question
from app.schemas.chat import PreparedTurn, TurnResult
from app.services.chat_context import load_context_plan
from app.services.chat_budget import context_notice
from app.services.chat_prewarm import PrewarmQueue, enqueue_prewarm
from app.services.chat_history import SessionFactory
from app.services.chat_turns import finish_turn, save_partial_answer, turn_is_active
from app.utils.sse import encode_event
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


# 新手注意：这份记录像一张进度卡，只保存正在等待的任务和下次检查时间。
@dataclass
class PendingStream:
    task: asyncio.Task
    next_check: float = 0
    last_partial_save_at: float = 0
    saved_content_length: int = 0


# 做什么：累积模型片段并限制输出大小，只认可明确的普通回答结束原因
# 入参：可变结果和经过结构校验的供应商片段
# 返回：本片段的可展示文字；未知结束原因或超长内容抛模型异常
def consume_chunk(result: TurnResult, chunk: ModelChunk) -> str:
    content = ""
    if chunk.usage is not None:
        result.prompt_tokens = chunk.usage.prompt_tokens
        result.completion_tokens = chunk.usage.completion_tokens
    if chunk.choices:
        choice = chunk.choices[0]
        content = choice.delta.content or ""
        if choice.finish_reason is not None:
            validate_finish_reason(choice.finish_reason)
            result.finish_reason = choice.finish_reason
    if len(result.content) + len(content) > get_settings().chat_max_answer_characters:
        raise ModelUnavailableError("回答超过长度限制，请缩小问题范围")
    result.content += content
    return content


# 做什么：拒绝工具调用等尚未支持的结束类型
# 入参：供应商返回的结束原因
# 返回：支持的原因正常结束；其他原因抛安全模型异常
def validate_finish_reason(finish_reason: str) -> None:
    if finish_reason not in SUPPORTED_FINISH_REASONS:
        raise ModelProtocolError("模型未能完成普通文本回答，请重新发送")


# 做什么：在等待模型时持续发送心跳，使浏览器关闭能及时触发传输断开
# 入参：会话工厂、生成上下文、模型迭代器和可变结果
# 返回：增量或心跳事件；生成被中断时正常结束且不标记完成
async def pump_chunks(sessions: SessionFactory, turn: PreparedTurn, iterator, result: TurnResult) -> AsyncIterator[str]:
    reader = PendingStream(task=asyncio.create_task(anext(iterator, None)), last_partial_save_at=monotonic())
    events = poll_chunks(sessions, turn, iterator, result, reader)
    try:
        async for event in events:
            yield event
    finally:
        reader.task.cancel()
        # 传输已经关闭时只清理等待任务，原来的模型异常仍由外层报告。
        with CancelScope(shield=True), suppress(asyncio.CancelledError, ModelUnavailableError):
            await events.aclose()
            await reader.task


# 做什么：按固定间隔检查中断通知，避免每个 token 都查询数据库
# 入参：等待进度、会话工厂和生成请求
# 返回：当前请求仍可生成返回 True，已中断返回 False
async def generation_is_current(reader: PendingStream, sessions: SessionFactory, turn: PreparedTurn) -> bool:
    if monotonic() < reader.next_check:
        return True
    reader.next_check = monotonic() + CHAT_STREAM_HEARTBEAT_SECONDS
    return await run_in_threadpool(turn_is_active, sessions, turn)


# 做什么：轮询下一个供应商片段，等待期间输出心跳
# 入参：会话工厂、生成请求、供应商迭代器、结果和等待进度
# 返回：增量或心跳事件；收到中断通知立即停止
async def poll_chunks(sessions: SessionFactory, turn: PreparedTurn, iterator,
                      result: TurnResult, reader: PendingStream) -> AsyncIterator[str]:
    while True:
        completed, _ = await asyncio.wait({reader.task}, timeout=CHAT_STREAM_HEARTBEAT_SECONDS)
        if not await generation_is_current(reader, sessions, turn):
            return
        event = read_stream_event(reader, result, bool(completed))
        if event is None:
            return
        if not await save_partial_if_due(sessions, turn, result, reader):
            return
        if event:
            yield event
        if completed:
            reader.task = asyncio.create_task(anext(iterator, None))


# 做什么：把等待结果转换成流事件，不在此处读写数据库
# 入参：当前等待任务、累积结果和是否已收到供应商片段
# 返回：文本事件、心跳或空字符串；模型完整结束返回 None
def read_stream_event(reader: PendingStream, result: TurnResult, chunk_ready: bool) -> str | None:
    if not chunk_ready:
        return encode_event("ping", {})
    chunk = reader.task.result()
    if chunk is None:
        mark_complete(result)
        return None
    content = consume_chunk(result, chunk)
    if content:
        return encode_event("delta", {"content": content})
    return ""


# 做什么：有新增文字且达到间隔时保存正文，数据库临时失败按同一间隔重试
# 入参：会话工厂、当前请求、已收到结果和上次保存进度
# 返回：可继续生成返回 True，已被中断返回 False；保存失败记录安全日志后继续
async def save_partial_if_due(sessions: SessionFactory, turn: PreparedTurn,
                              result: TurnResult, reader: PendingStream) -> bool:
    current_time = monotonic()
    if len(result.content) <= reader.saved_content_length:
        return True
    if current_time - reader.last_partial_save_at < get_settings().chat_partial_save_interval_seconds:
        return True
    # 新手注意：快照像写作时定期点保存，只保存当前稿件，不表示文章已经写完。
    reader.last_partial_save_at = current_time
    try:
        saved = await run_in_threadpool(save_partial_answer, sessions, turn, result.content)
    except SQLAlchemyError:
        logger.error("部分回答保存失败，将按间隔重试 | session_id=%s | request_id=%s", turn.session_id, turn.request_id)
        return True
    if saved:
        reader.saved_content_length = len(result.content)
    return saved


# 做什么：只有收到结束标记、结束原因和非空正文才认定为完成
# 入参：已经累积的模型结果
# 返回：无，校验通过后更新状态，否则抛模型异常
def mark_complete(result: TurnResult) -> None:
    if not result.finish_reason or not result.content.strip():
        raise ModelProtocolError("模型未返回完整回答，请重新发送")
    result.status = MessageStatus.COMPLETED
    result.error_code = None


# 做什么：对整个生成设置总时限，包含连接、重试、等待片段和上下文读取
# 入参：会话工厂、生成上下文和可变结果
# 返回：模型增量流；达到总时限抛 TimeoutError
async def timed_events(sessions: SessionFactory, turn: PreparedTurn, result: TurnResult) -> AsyncIterator[str]:
    events = model_events(sessions, turn, result)
    async with asyncio.timeout(get_settings().llm_timeout_seconds), aclosing(events):
        async for event in events:
            yield event


# 做什么：在打开模型前准备资料和历史，并显式清理两层迭代器
# 入参：会话工厂、生成请求和可变结果
# 返回：模型事件流，提前关闭也会清理上游请求
async def model_events(sessions: SessionFactory, turn: PreparedTurn, result: TurnResult) -> AsyncIterator[str]:
    bundle = ReferenceBundle(reason="no_selection")
    if turn.knowledge_base_id is not None:
        bundle = await collect_references(sessions, turn.user_id, turn.knowledge_base_id, turn.content)
    log_references(turn, bundle)
    plan = await run_in_threadpool(load_context_plan, sessions, turn, bundle.block)
    if plan.history_truncated:
        yield encode_event("context", context_notice(plan))
    if bundle.items:
        yield encode_event("references", {"items": bundle.items})
    iterator = stream_model(plan.messages, compose_question(bundle.block, turn.content))
    events = pump_chunks(sessions, turn, iterator, result)
    try:
        async for event in events:
            yield event
    finally:
        try:
            await close_stream(events, turn, "chunk_events")
        finally:
            await close_stream(iterator, turn, "model_stream")


# 做什么：记录单轮检索的统计与降级原因，不记录问题或资料
# 入参：生成请求和引用结果
# 返回：无；超时和数据库异常用警告级别记录
def log_references(turn: PreparedTurn, bundle: ReferenceBundle) -> None:
    degraded = bundle.reason in ("timeout", "db_error")
    log_level = logging.WARNING if degraded else logging.INFO
    block_bytes = len(bundle.block.encode("utf-8"))
    logger.log(
        log_level, "知识库检索 | session_id=%s | knowledge_base_id=%s | hit_count=%s | kept_count=%s | "
        "block_bytes=%s | elapsed_ms=%.1f | degraded=%s | reason=%s",
        turn.session_id, turn.knowledge_base_id, bundle.hit_count, len(bundle.items),
        block_bytes, bundle.elapsed_ms, degraded, bundle.reason,
    )


# 做什么：独立关闭一个流资源，清理失败只记录安全信息，不阻断其他收尾
# 入参：流迭代器、当前生成请求和资源名称
# 返回：无；普通关闭异常记录日志，取消异常继续传播
async def close_stream(iterator, turn: PreparedTurn, resource_name: str) -> None:
    with CancelScope(shield=True):
        try:
            await iterator.aclose()
        except Exception as error:
            logger.error("流资源清理失败 | user_id=%s | session_id=%s | request_id=%s | resource=%s | error_type=%s",
                         turn.user_id, turn.session_id, turn.request_id, resource_name, type(error).__name__)


# 做什么：在取消作用域之外完成持久化，保证关闭页面后仍能标记中断
# 入参：会话工厂、生成上下文和最终结果
# 返回：成功结束返回 True，被中断返回 False，数据库故障记录错误并返回 None
async def persist_result(sessions: SessionFactory, turn: PreparedTurn, result: TurnResult) -> bool | None:
    # 新手注意：屏蔽取消像让收银员先记完账，再接受离场通知；只保护短数据库操作。
    try:
        with CancelScope(shield=True):
            return await run_in_threadpool(finish_turn, sessions, turn, result)
    except SQLAlchemyError:
        result.status, result.error_code = MessageStatus.FAILED, "storage_unavailable"
        logger.error("生成结果保存失败 | session_id=%s | request_id=%s", turn.session_id, turn.request_id)
        return None


# 做什么：统一编排流事件，只有数据库保存成功后才发送 completed
# 入参：会话工厂和已经接收的问题上下文
# 返回：started/delta/completed/error/interrupted 流，取消时保存部分回答并重新抛取消
async def stream_turn(sessions: SessionFactory, turn: PreparedTurn, prewarm_queue: PrewarmQueue | None = None) -> AsyncIterator[str]:
    started_at = monotonic()
    result = TurnResult(status=MessageStatus.INTERRUPTED, error_code="client_disconnected")
    error_message = "生成已中断"
    events = timed_events(sessions, turn, result)
    try:
        yield encode_event("started", {"request_id": turn.request_id, "assistant_id": turn.assistant_id})
        async for event in events:
            yield event
    except (ModelUnavailableError, TimeoutError) as error:
        model_error = ModelTimeoutError() if isinstance(error, TimeoutError) else error
        result.status, result.error_code = MessageStatus.FAILED, model_error.error_code
        error_message = model_error.message
    except asyncio.CancelledError:
        result.status = MessageStatus.INTERRUPTED
        raise
    except Exception as error:
        result.status, result.error_code = MessageStatus.FAILED, "generation_failed"
        error_message = "生成失败，请稍后重新发送"
        logger.error("流式生成异常 | session_id=%s | request_id=%s | error_type=%s",
                     turn.session_id, turn.request_id, type(error).__name__)
    finally:
        # 清理和持久化独立收尾，即使上游关闭抛出取消异常，也必须尝试保存正文。
        try:
            await close_stream(events, turn, "response_events")
        finally:
            saved = await persist_result(sessions, turn, result)
            log_generation(turn, result, started_at)
    yield await final_event(sessions, turn, result, saved, error_message, prewarm_queue)


# 做什么：记录一次生成的安全摘要，不打印问题、回答、凭证或完整上游异常
# 入参：请求、结果和开始时间
# 返回：无，日志自带模块名
def log_generation(turn: PreparedTurn, result: TurnResult, started_at: float) -> None:
    logger.info("模型调用结束 | user_id=%s | session_id=%s | request_id=%s | model=%s | "
                "elapsed_seconds=%.2f | input_characters=%s | output_characters=%s | "
                "prompt_tokens=%s | completion_tokens=%s | status=%s | error_code=%s",
                turn.user_id, turn.session_id, turn.request_id, get_settings().llm_model,
                monotonic() - started_at, len(turn.content), len(result.content),
                result.prompt_tokens, result.completion_tokens, result.status, result.error_code)


# 做什么：把已持久化的结果转为最终事件，只提交预热，不等待缓存
# 入参：会话工厂、请求、结果、是否保存和安全错误文案
# 返回：完成、失败或中断事件，绝不把未保存回答标记完成
async def final_event(sessions: SessionFactory, turn: PreparedTurn, result: TurnResult,
                      saved: bool | None, error_message: str, prewarm_queue: PrewarmQueue | None = None) -> str:
    if saved is None:
        return encode_event("error", {"code": 5000, "message": "回答未能保存，请稍后刷新历史确认"})
    if not saved or result.status == MessageStatus.INTERRUPTED:
        return encode_event("interrupted", {"message": "生成已中断"})
    if result.status == MessageStatus.FAILED:
        return encode_event("error", {"code": MODEL_EVENT_CODES.get(result.error_code, ModelUnavailableError.code),
                                      "message": error_message})
    enqueue_prewarm(prewarm_queue, sessions, turn)
    return encode_event("completed", {"assistant_id": turn.assistant_id,
                                       "finish_reason": result.finish_reason})

