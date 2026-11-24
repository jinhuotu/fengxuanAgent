"""本人对话接口；SQL、归属和生成状态全部交给服务层。"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask

from app.api.deps import get_access_token
from app.core.chat import CHAT_SSE_HEADERS
from app.db import session as database_session
from app.schemas.auth import UserInfo
from app.schemas.chat import ChatPageQuery, ChatSendRequest, ChatSessionKnowledgeBaseUpdate
from app.services import chat_history, chat_knowledge, chat_turns, chat_requests
from app.services.chat_history import SessionFactory
from app.services.chat_stream import stream_turn
from app.services.chat_prewarm import PrewarmQueue
from app.utils.response import success
from app.utils.sse import ClosingStreamingResponse

router = APIRouter(prefix="/chat", tags=["AI 对话"])


# 做什么：提供项目既有会话工厂，方便测试替换隔离数据库
# 入参：无
# 返回：会话工厂，不提前打开连接
def get_chat_sessions() -> SessionFactory:
    return database_session.SessionLocal


# 做什么：取得本应用进程的可选预热队列
# 入参：FastAPI 请求
# 返回：队列或 None；生命周期未启用时仍可正常生成
def get_prewarm_queue(request: Request) -> PrewarmQueue | None:
    return getattr(request.app.state, "chat_prewarm_queue", None)


# 做什么：在短数据库会话中校验用户，避免流式请求长期持有鉴权连接
# 入参：Bearer 凭证和会话工厂
# 返回：公开用户信息；身份失效抛既有 401 或 403
def get_chat_user(token: str = Depends(get_access_token), sessions=Depends(get_chat_sessions)) -> UserInfo:
    return chat_history.resolve_chat_user(sessions, token)


# 做什么：创建本人的空会话
# 入参：已验证用户和会话工厂
# 返回：统一成功响应，包含会话公开字段
@router.post("/sessions", summary="创建本人对话")
def create_session_endpoint(current_user: UserInfo = Depends(get_chat_user), sessions=Depends(get_chat_sessions)) -> dict:
    chat_session = chat_history.create_session(sessions, current_user.id)
    return success(chat_session.model_dump(mode="json"))


# 做什么：分页查询本人的会话
# 入参：分页参数、已验证用户和会话工厂
# 返回：统一分页响应，不包含其他用户的会话
@router.get("/sessions", summary="本人会话列表")
def list_sessions_endpoint(pagination: ChatPageQuery = Query(), current_user: UserInfo = Depends(get_chat_user),
                            sessions=Depends(get_chat_sessions)) -> dict:
    return success(chat_history.list_sessions(sessions, current_user.id, pagination))


# 做什么：为本人的空闲会话选择知识库或解除选择
# 入参：会话编号、可为空的知识库编号、当前用户和会话工厂
# 返回：统一响应中的会话公开字段；会话和库的校验失败走既有错误响应
@router.put("/sessions/{session_id}/knowledge-base", summary="更新会话所选知识库")
def set_session_knowledge_base_endpoint(
    session_id: int, payload: ChatSessionKnowledgeBaseUpdate,
    current_user: UserInfo = Depends(get_chat_user), sessions=Depends(get_chat_sessions),
) -> dict:
    chat_session = chat_knowledge.set_session_knowledge_base(
        sessions, current_user.id, session_id, payload.knowledge_base_id
    )
    return success(chat_session.model_dump(mode="json"))


# 做什么：分页读取本人指定会话的消息
# 入参：会话编号、分页参数、已验证用户和会话工厂
# 返回：统一分页响应；不存在或非本人会话返回 404
@router.get("/sessions/{session_id}/messages", summary="本人会话历史")
def list_messages_endpoint(session_id: int, pagination: ChatPageQuery = Query(),
                            current_user: UserInfo = Depends(get_chat_user), sessions=Depends(get_chat_sessions)) -> dict:
    return success(chat_history.list_messages(sessions, current_user.id, session_id, pagination))


# 做什么：接收问题并返回 SSE，所有数据库操作交给线程池中的服务
# 入参：会话编号、问题、请求编号和当前用户
# 返回：流式响应；接收失败时仍返回既有统一 JSON 错误
@router.post("/sessions/{session_id}/messages", summary="流式发送问题")
async def send_message_endpoint(session_id: int, payload: ChatSendRequest,
                                current_user: UserInfo = Depends(get_chat_user), sessions=Depends(get_chat_sessions),
                                prewarm_queue=Depends(get_prewarm_queue)):
    turn = await run_in_threadpool(chat_turns.prepare_turn, sessions, current_user.id, session_id,
                                  str(payload.request_id), payload.content,
                                  str(payload.retry_of_request_id) if payload.retry_of_request_id else None)
    cleanup = BackgroundTask(chat_turns.interrupt_turn, sessions, current_user.id, session_id, turn.request_id)
    return ClosingStreamingResponse(stream_turn(sessions, turn, prewarm_queue), media_type="text/event-stream",
                                    headers=CHAT_SSE_HEADERS, background=cleanup)


# 做什么：页面离开或用户点击停止时中断本人的指定生成
# 入参：会话编号、请求编号和当前用户
# 返回：是否实际中断；重复调用安全，不会中断后来发起的新请求
@router.post("/sessions/{session_id}/requests/{request_id}/interrupt", summary="中断本人生成")
def interrupt_endpoint(session_id: int, request_id: UUID, current_user: UserInfo = Depends(get_chat_user),
                         sessions=Depends(get_chat_sessions)) -> dict:
    interrupted = chat_turns.interrupt_turn(sessions, current_user.id, session_id, str(request_id))
    return success({"interrupted": interrupted})


# 做什么：直接查询本人的某轮请求及可重试状态
# 入参：会话编号、请求 UUID、当前用户和会话工厂
# 返回：统一结果；未提交记录或越权返回 404，不触发模型生成
@router.get("/sessions/{session_id}/requests/{request_id}", summary="查询本人请求结果")
def request_result_endpoint(session_id: int, request_id: UUID, response: Response, current_user: UserInfo = Depends(get_chat_user),
                            sessions=Depends(get_chat_sessions)) -> dict:
    result = chat_requests.get_request_result(sessions, current_user.id, session_id, str(request_id))
    response.headers["Cache-Control"] = "no-store"
    return success(result.model_dump(mode="json"))


# 做什么：查询本人会话当前有效占用及历史版本，供其他页面校准
# 入参：会话编号、当前用户和会话工厂
# 返回：统一会话状态，不返回其他用户内容或触发模型生成
@router.get("/sessions/{session_id}/state", summary="查询本人会话状态")
def session_state_endpoint(session_id: int, response: Response, current_user: UserInfo = Depends(get_chat_user),
                            sessions=Depends(get_chat_sessions)) -> dict:
    state = chat_history.get_session_state(sessions, current_user.id, session_id)
    response.headers["Cache-Control"] = "no-store"
    return success(state.model_dump(mode="json"))

