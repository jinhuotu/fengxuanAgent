"""用独立 SQLite、替身检索和替身模型验证会话知识库。"""
import logging
from time import sleep
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.chat import MessageStatus
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db import session as database_session
from app.llm import client as model_client
from app.llm.tokens import estimate_request_tokens
from app.main import app
from app.models.chat import ChatSession
from app.models.knowledge import KnowledgeBase
from app.rag import retrieval
from app.schemas.chat import TurnResult
from app.services import chat_stream
from app.services.chat_turns import finish_turn, prepare_turn
from tests.chat_support import event_items, file_chat_sessions, history_items

SESSIONS_URL = "/chat/sessions"
OWNER_ID = 1
OTHER_USER_ID = 2
OWN_BASE_ID = 31
OTHER_BASE_ID = 32
DELETED_BASE_ID = 33
QUESTION = "窑炉升温应如何控制？"
REFERENCE_CONTENT = "分段升温，逐段记录设定温度和实际读数。"
REFERENCE = {
    "index": 1, "chunk_id": 8, "document_id": 3, "heading_path": "窑炉工艺手册 > 第3段",
    "char_start": 12, "char_end": 12 + len(REFERENCE_CONTENT), "score": 2.17,
}


# 做什么：配置文件库和登录客户，并创建本人、他人及已删除的库
# 入参：隔离数据库会话工厂和 pytest 替换工具
# 返回：已登录的 HTTP 客户端；退出时关闭客户端
@pytest.fixture
def rag_client(file_chat_sessions, monkeypatch):
    monkeypatch.setattr(database_session, "SessionLocal", file_chat_sessions)
    monkeypatch.setattr(get_settings(), "chat_prewarm_enabled", False)
    bases = [
        KnowledgeBase(id=OWN_BASE_ID, owner_id=OWNER_ID, name="本人库"),
        KnowledgeBase(id=OTHER_BASE_ID, owner_id=OTHER_USER_ID, name="他人库"),
        KnowledgeBase(id=DELETED_BASE_ID, owner_id=OWNER_ID, name="删除库", is_deleted=1),
    ]
    with file_chat_sessions.begin() as session:
        session.add_all(bases)
    with TestClient(app, headers={"Authorization": f"Bearer {create_access_token(OWNER_ID)}"}) as client:
        yield client


# 做什么：提供可观察的检索替身，避免在 SQLite 执行全文查询
# 入参：pytest 替换工具
# 返回：默认命中一条验收资料的替身
@pytest.fixture
def search_stub(monkeypatch):
    hit = {field: value for field, value in REFERENCE.items() if field != "index"}
    hit["content"] = REFERENCE_CONTENT
    search = Mock(return_value=[hit])
    monkeypatch.setattr(retrieval, "search_chunks", search)
    return search


# 做什么：记录生成计划和真实格式的模型输入，替换付费模型流
# 入参：pytest 替换工具
# 返回：按生成顺序记录的计划和模型消息数组
@pytest.fixture
def recorded_generation(monkeypatch):
    plans = []
    model_messages = []
    original_plan_loader = chat_stream.load_context_plan

    # 做什么：保留经过原预算计算的当轮计划
    # 入参：数据库会话工厂、生成上下文及可选资料块
    # 返回：原预算计划；预算错误继续抛出
    def record_plan(sessions, turn, reference_block=""):
        plan = original_plan_loader(sessions, turn, reference_block)
        plans.append(plan)
        return plan

    # 做什么：用原格式构造模型请求并返回一段替身回答
    # 入参：当轮历史及当前用户消息
    # 返回：一段完整模型响应；实际预算检查失败会抛出
    async def record_model(history, question):
        model_messages.append(model_client.model_payload(history, question)["messages"])
        yield model_client.ModelChunk.model_validate({
            "choices": [{"delta": {"content": "按段记录温度。"}, "finish_reason": "stop"}],
        })

    monkeypatch.setattr(chat_stream, "load_context_plan", record_plan)
    monkeypatch.setattr(chat_stream, "stream_model", record_model)
    return plans, model_messages


# 做什么：通过接口建立一个空白会话
# 入参：已登录的隔离客户端
# 返回：公开会话对象；接口失败时断言失败
def new_session(client):
    response = client.post(SESSIONS_URL)
    assert response.status_code == 200
    return response.json()["data"]


# 做什么：请求更新当前会话的选库
# 入参：客户端、会话编号及可为空的知识库编号
# 返回：未经处理的接口响应
def select_base(client, session_id, base_id):
    return client.put(f"{SESSIONS_URL}/{session_id}/knowledge-base", json={"knowledge_base_id": base_id})


# 做什么：通过真实路由发问并解析整轮 SSE
# 入参：已登录的客户端和会话编号
# 返回：有序事件列表；接口失败时断言失败
def submit_question(client, session_id):
    payload = {"request_id": str(uuid4()), "content": QUESTION}
    response = client.post(f"{SESSIONS_URL}/{session_id}/messages", json=payload)
    assert response.status_code == 200
    return event_items(response.text)


# 做什么：灌入两轮相同的验收历史以对比选库前后的模型输入
# 入参：隔离会话工厂及目标会话编号
# 返回：无；无法保存完整问答时断言失败
def seed_two_rounds(sessions, session_id):
    for round_number in (1, 2):
        turn = prepare_turn(sessions, OWNER_ID, session_id, str(uuid4()), f"历史问题{round_number}")
        result = TurnResult(status=MessageStatus.COMPLETED, content=f"历史回答{round_number}")
        assert finish_turn(sessions, turn, result)


# 做什么：确认选库能回填，清除选择不改变历史版本或更新时间
# 入参：已登录的隔离客户端
# 返回：无；接口或会话字段不符时断言失败
def test_session_selection_is_visible_and_can_be_cleared(rag_client):
    original = new_session(rag_client)
    response = select_base(rag_client, original["id"], OWN_BASE_ID)
    assert response.status_code == 200
    selected = response.json()["data"]
    assert selected == {**original, "knowledge_base_id": OWN_BASE_ID}
    listed = rag_client.get(SESSIONS_URL).json()["data"]["items"]
    assert listed == [selected]
    cleared = select_base(rag_client, original["id"], None)
    assert cleared.status_code == 200 and cleared.json()["data"] == original


# 做什么：确认选择不可见的知识库时保留已经选中的库
# 入参：隔离客户端及他人或已删除的库编号
# 返回：无；未返回 404/4001 或改变选择时断言失败
@pytest.mark.parametrize("base_id", [OTHER_BASE_ID, DELETED_BASE_ID])
def test_selecting_inaccessible_base_preserves_selection(rag_client, base_id):
    session_id = new_session(rag_client)["id"]
    assert select_base(rag_client, session_id, OWN_BASE_ID).status_code == 200
    response = select_base(rag_client, session_id, base_id)
    assert response.status_code == 404 and response.json()["code"] == 4001
    listed = rag_client.get(SESSIONS_URL).json()["data"]["items"]
    assert listed[0]["knowledge_base_id"] == OWN_BASE_ID


# 做什么：确认生成占用存在时连清除选择也会被拒绝
# 入参：隔离客户端和文件库会话工厂
# 返回：无；占用或选库发生变化时断言失败
def test_busy_session_cannot_change_knowledge_base(rag_client, file_chat_sessions):
    session_id = new_session(rag_client)["id"]
    assert select_base(rag_client, session_id, OWN_BASE_ID).status_code == 200
    turn = prepare_turn(file_chat_sessions, OWNER_ID, session_id, str(uuid4()), QUESTION)
    assert turn.knowledge_base_id == OWN_BASE_ID
    response = select_base(rag_client, session_id, None)
    assert response.status_code == 409 and response.json()["code"] == 3002
    with file_chat_sessions() as session:
        chat_session = session.get(ChatSession, session_id)
        assert chat_session.active_request_id == turn.request_id
        assert chat_session.knowledge_base_id == OWN_BASE_ID


# 做什么：确认不可见会话先返回自己的 404 错误
# 入参：隔离客户端和另一用户的身份
# 返回：无；未返回 404/3001 时断言失败
def test_other_users_session_cannot_be_changed(rag_client):
    alternate_headers = {"Authorization": f"Bearer {create_access_token(OTHER_USER_ID)}"}
    other_session = rag_client.post(SESSIONS_URL, headers=alternate_headers).json()["data"]
    response = select_base(rag_client, other_session["id"], OWN_BASE_ID)
    assert response.status_code == 404 and response.json()["code"] == 3001


# 做什么：检查未选库的生成对检索零调用，模型收到的原问题不变
# 入参：隔离客户端、检索替身和生成记录
# 返回：无；出现任何引用或调用时断言失败
def test_no_selection_preserves_original_turn(rag_client, search_stub, recorded_generation):
    events = submit_question(rag_client, new_session(rag_client)["id"])
    plans, model_messages = recorded_generation
    search_stub.assert_not_called()
    assert [name for name, _ in events] == ["started", "delta", "completed"]
    assert plans[0].reference_block == "" and plans[0].reference_bytes == 0
    assert model_messages[0][-1] == {"role": "user", "content": QUESTION}


# 做什么：检查命中引用顺序、原始历史和模型最后一条用户消息
# 入参：隔离客户端、文件库、检索替身、生成和配置替换工具
# 返回：无；引用元数据、预算或原文位置不符时断言失败
def test_selected_base_sends_references_before_first_delta(
    rag_client, file_chat_sessions, search_stub, recorded_generation, monkeypatch,
):
    monkeypatch.setattr(get_settings(), "chat_context_rounds", 1)
    selected_id = new_session(rag_client)["id"]
    plain_id = new_session(rag_client)["id"]
    for session_id in (selected_id, plain_id):
        seed_two_rounds(file_chat_sessions, session_id)
    assert select_base(rag_client, selected_id, OWN_BASE_ID).status_code == 200
    selected_events = submit_question(rag_client, selected_id)
    plain_events = submit_question(rag_client, plain_id)
    assert [name for name, _ in selected_events] == ["started", "context", "references", "delta", "completed"]
    assert [name for name, _ in plain_events] == ["started", "context", "delta", "completed"]
    assert selected_events[2][1] == {"items": [REFERENCE]}
    plans, calls = recorded_generation
    selected_plan, plain_plan = plans
    assert selected_plan.messages == plain_plan.messages and len(plain_plan.messages) == 2
    assert selected_plan.reference_block == f"[1] {REFERENCE['heading_path']}\n{REFERENCE_CONTENT}"
    assert selected_plan.reference_bytes == len(selected_plan.reference_block.encode("utf-8"))
    assert calls[0][:-1] == calls[1][:-1]
    assert calls[0][-1]["content"].endswith(f"{REFERENCE_CONTENT}\n\n我的问题：\n{QUESTION}")
    assert calls[1][-1]["content"] == QUESTION
    assert history_items(file_chat_sessions, selected_id)[-2]["content"] == QUESTION
    search_stub.assert_called_once()
    assert search_stub.call_args.args[1:] == (OWNER_ID, OWN_BASE_ID, QUESTION, 5)


# 做什么：模拟超过默认等待上限的同步查询
# 入参：任意标准检索参数，替身不执行数据库查询
# 返回：等待结束后的空命中
def slow_search(db_session, owner_id, knowledge_base_id, question, top_k):
    sleep(0.95)
    return []


# 做什么：验证数据库故障或超时只触发可观察的检索降级
# 入参：隔离客户端、检索替身、模型记录、日志及故障类型
# 返回：无；错误中断回答、日志泄漏或错误分类时断言失败
@pytest.mark.parametrize("reason", ["db_error", "timeout"])
def test_retrieval_failure_does_not_break_stream(
    rag_client, search_stub, recorded_generation, caplog, reason,
):
    failure = SQLAlchemyError("private-query-details")
    search_stub.side_effect = slow_search if reason == "timeout" else failure
    session_id = new_session(rag_client)["id"]
    assert select_base(rag_client, session_id, OWN_BASE_ID).status_code == 200
    with caplog.at_level(logging.WARNING, logger=chat_stream.__name__):
        events = submit_question(rag_client, session_id)
    assert [name for name, _ in events] == ["started", "delta", "completed"]
    assert f"degraded=True | reason={reason}" in caplog.text
    assert "private-query-details" not in caplog.text and QUESTION not in caplog.text
    plans, model_messages = recorded_generation
    assert plans[0].reference_block == "" and model_messages[0][-1]["content"] == QUESTION
    search_stub.assert_called_once()


# 做什么：验证参考资料无法进入总预算时仍保留完整问题并完成回答
# 入参：隔离客户端、替身检索、模型记录和配置替换工具
# 返回：无；出现超预算或错误引用时断言失败
def test_reference_block_respects_remaining_input_budget(rag_client, search_stub, recorded_generation, monkeypatch):
    budget = estimate_request_tokens(model_client.SYSTEM_PROMPT, [], QUESTION) + 8
    monkeypatch.setattr(get_settings(), "chat_input_token_budget", budget)
    session_id = new_session(rag_client)["id"]
    assert select_base(rag_client, session_id, OWN_BASE_ID).status_code == 200
    events = submit_question(rag_client, session_id)
    assert [name for name, _ in events] == ["started", "delta", "completed"]
    plans, model_messages = recorded_generation
    assert plans[0].reference_bytes == 0 and plans[0].estimated_input_tokens <= budget
    assert model_messages[0][-1]["content"] == QUESTION
    search_stub.assert_called_once()
