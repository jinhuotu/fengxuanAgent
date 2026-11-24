"""测试真实接口装配、JWT、归属和流式事件。"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import session as database_session
from app.main import app
from app.services import chat_stream
from tests.chat_support import chat_sessions
from tests.chat_support import new_turn
from app.core.config import get_settings
from tests.test_chat_stream import completed_model


# 做什么：把真实接口的数据库替换成独立内存工厂
# 入参：隔离数据库和替换工具
# 返回：真实 FastAPI 测试客户端，不访问生产数据库
@pytest.fixture
def chat_client(chat_sessions, monkeypatch):
    monkeypatch.setattr(database_session, "SessionLocal", chat_sessions)
    monkeypatch.setattr(chat_stream, "stream_model", completed_model)
    with TestClient(app) as client:
        yield client


# 做什么：为隔离账号生成真实 Bearer 请求头
# 入参：账号编号
# 返回：认证请求头，不输出完整 token
def user_headers(user_id):
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


# 做什么：确认未登录拒绝、本人可使用、其他用户无法读取或发送
# 入参：真实测试客户端
# 返回：无；接口鉴权或统一错误结构不符时失败
def test_chat_endpoints_enforce_identity_and_ownership(chat_client):
    assert chat_client.get("/chat/sessions").status_code == 401
    created = chat_client.post("/chat/sessions", headers=user_headers(1))
    session_id = created.json()["data"]["id"]
    stranger_history = chat_client.get(f"/chat/sessions/{session_id}/messages", headers=user_headers(2))
    assert stranger_history.status_code == 404
    assert stranger_history.json()["code"] == 3001
    payload = {"request_id": str(uuid4()), "content": "你好"}
    denied = chat_client.post(f"/chat/sessions/{session_id}/messages", json=payload, headers=user_headers(2))
    assert denied.status_code == 404
    assert chat_client.get("/chat/sessions", headers=user_headers(2)).json()["data"]["items"] == []


# 做什么：验证真实 SSE 完成和历史恢复，并拒绝重复发送和非法输入
# 入参：真实测试客户端
# 返回：无；完成结果必须能从 MySQL 访问层恢复
def test_chat_stream_roundtrip_and_validation(chat_client):
    headers = user_headers(1)
    session_id = chat_client.post("/chat/sessions", headers=headers).json()["data"]["id"]
    path = f"/chat/sessions/{session_id}/messages"
    payload = {"request_id": str(uuid4()), "content": "你好"}
    response = chat_client.post(path, json=payload, headers=headers)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: completed" in response.text
    messages = chat_client.get(path, headers=headers).json()["data"]["items"]
    assert messages[-1]["status"] == "completed"
    assert messages[-1]["content"] == "你好，世界"
    assert chat_client.post(path, json=payload, headers=headers).status_code == 409
    assert chat_client.post(path, json={"request_id": str(uuid4()), "content": "   "}, headers=headers).status_code == 422
    assert chat_client.get(path, params={"page_size": 101}, headers=headers).status_code == 422


# 做什么：确认满额在 SSE 开始前返回统一 429，历史中不增加虚假轮次
# 入参：真实客户端、隔离工厂和替换工具
# 返回：无；错误协议或写入多余消息时失败
def test_capacity_rejection_is_json_before_stream(chat_client, chat_sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_global_concurrency_limit", 1)
    new_turn(chat_sessions)
    headers = user_headers(1)
    session_id = chat_client.post("/chat/sessions", headers=headers).json()["data"]["id"]
    path = f"/chat/sessions/{session_id}/messages"
    response = chat_client.post(path, json={"request_id": str(uuid4()), "content": "满额问题"}, headers=headers)
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == 3004
    assert chat_client.get(path, headers=headers).json()["data"]["items"] == []


# 做什么：确认可直接查询已提交的请求，不依赖历史分页
# 入参：真实客户端和隔离工厂
# 返回：无；接口不存在、请求定位或归属错误时失败
def test_query_request_returns_owned_question_and_answer(chat_client, chat_sessions):
    turn = new_turn(chat_sessions)
    path = f"/chat/sessions/{turn.session_id}/requests/{turn.request_id}"
    response = chat_client.get(path, headers=user_headers(1))
    assert response.status_code == 200
    result = response.json()["data"]
    assert response.headers.get("cache-control") == "no-store"
    assert result["question"]["content"] == turn.content
    assert result["answer"]["status"] == "generating"
    assert result["can_retry"] is False
    assert chat_client.get(path, headers=user_headers(2)).status_code == 404


# 做什么：验证查询失败轮次后按原编号关联重试，新结果完成而旧记录保留
# 入参：真实接口客户端和隔离工厂
# 返回：无；接口字段、重试资格或旧记录保护失效时失败
def test_manual_retry_api_preserves_original_attempt(chat_client, chat_sessions):
    from app.core.chat import MessageStatus
    from app.schemas.chat import TurnResult
    from app.services.chat_turns import finish_turn
    turn = new_turn(chat_sessions)
    finish_turn(chat_sessions, turn, TurnResult(status=MessageStatus.FAILED, content="旧部分回答"))
    headers = user_headers(1)
    request_path = f"/chat/sessions/{turn.session_id}/requests/{turn.request_id}"
    assert chat_client.get(request_path, headers=headers).json()["data"]["can_retry"]
    retry_id = str(uuid4())
    payload = {"request_id": retry_id, "content": turn.content, "retry_of_request_id": turn.request_id}
    response = chat_client.post(f"/chat/sessions/{turn.session_id}/messages", json=payload, headers=headers)
    assert "event: completed" in response.text
    original = chat_client.get(request_path, headers=headers).json()["data"]
    assert original["answer"]["content"] == "旧部分回答"
    assert original["answer"]["status"] == "failed"
    assert not original["can_retry"]
    retry_path = f"/chat/sessions/{turn.session_id}/requests/{retry_id}"
    assert chat_client.get(retry_path, headers=headers).json()["data"]["answer"]["status"] == "completed"


# 做什么：确认输入预算不足与输出预留配置错误在 SSE 开始前拒绝
# 入参：真实客户端、替换工具和不足类型
# 返回：无；预算错误仍写入消息或被误作未知服务器故障时失败
@pytest.mark.parametrize("configuration_error", [False, True])
def test_budget_rejection_before_stream_leaves_no_messages(chat_client, monkeypatch, configuration_error):
    settings = get_settings()
    error_code = 5011
    if configuration_error:
        monkeypatch.setattr(settings, "llm_context_window_tokens", 1)
        error_code = 5012
    else:
        monkeypatch.setattr(settings, "chat_input_token_budget", 1)
    headers = user_headers(1)
    session_id = chat_client.post("/chat/sessions", headers=headers).json()["data"]["id"]
    path = f"/chat/sessions/{session_id}/messages"
    response = chat_client.post(path, headers=headers, json={"request_id": str(uuid4()), "content": "完整的问题"})
    assert response.json()["code"] == error_code
    assert response.status_code in {400, 503}
    assert response.headers["content-type"].startswith("application/json")
    assert chat_client.get(path, headers=headers).json()["data"]["items"] == []


# 做什么：复现其他页面无法查询有效活动请求与历史版本
# 入参：真实客户端和隔离工厂
# 返回：无；状态接口缺失或泄露其他用户状态时失败
def test_session_state_exposes_owned_active_request(chat_client, chat_sessions):
    turn = new_turn(chat_sessions)
    path = f"/chat/sessions/{turn.session_id}/state"
    response = chat_client.get(path, headers=user_headers(1))
    assert response.status_code == 200
    state = response.json()["data"]
    assert response.headers.get("cache-control") == "no-store"
    assert state["active_request_id"] == turn.request_id
    assert state["history_version"] == 0
    assert chat_client.get(path, headers=user_headers(2)).status_code == 404


# 做什么：确认状态查询主动恢复过期占用，版本前进且保留快照
# 入参：真实客户端和独立数据库
# 返回：无；过期仍被视为生成、快照丢失或版本不一致时失败
def test_session_state_recovers_expired_request(chat_client, chat_sessions):
    from datetime import timedelta
    from app.models.chat import ChatMessage, ChatSession
    from app.services.chat_history import current_chat_time
    turn = new_turn(chat_sessions)
    with chat_sessions.begin() as db_session:
        db_session.get(ChatSession, turn.session_id).generation_expires_at = current_chat_time() - timedelta(seconds=1)
        db_session.get(ChatMessage, turn.assistant_id).content = "观察页可恢复的快照"
    headers = user_headers(1)
    path = f"/chat/sessions/{turn.session_id}"
    listed = chat_client.get("/chat/sessions", headers=headers).json()["data"]["items"][0]
    assert listed["active_request_id"] is None
    state = chat_client.get(f"{path}/state", headers=headers).json()["data"]
    assert state["active_request_id"] is None
    assert state["generation_expires_at"] is None
    assert state["history_version"] == 1
    history = chat_client.get(f"{path}/messages", headers=headers).json()["data"]
    assert history["session"]["history_version"] == 1
    assert history["items"][-1]["status"] == "interrupted"
    assert history["items"][-1]["content"] == "观察页可恢复的快照"


# 做什么：确认状态接口不返回正文且登录与软删除仍受保护
# 入参：真实客户端和独立数据库
# 返回：无；未登录或软删除读取成功时失败
def test_session_state_requires_login_and_hides_deleted_session(chat_client, chat_sessions):
    from app.models.chat import ChatSession
    turn = new_turn(chat_sessions)
    path = f"/chat/sessions/{turn.session_id}/state"
    assert chat_client.get(path).status_code == 401
    state = chat_client.get(path, headers=user_headers(1)).json()["data"]
    assert "messages" not in state
    assert "content" not in state
    with chat_sessions.begin() as db_session:
        db_session.get(ChatSession, turn.session_id).is_deleted = 1
    assert chat_client.get(path, headers=user_headers(1)).status_code == 404

