"""聊天 API，支持流式与非流式。

阶段一要求：
- stream=true 时也必须走工具调用链路（与非流式行为一致），并确保
  turn_count/notice 与非流式一致。
- 工具调用过程对前端透明：SSE 只输出最终对话结果的分段与元信息。
"""

import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.chat import ChatSession
from app.models.knowledge_base import KnowledgeBase
from app.models.model_config import ModelConfig
from app.models.prompt import PromptTemplate
from app.models.user import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.response import ok
from app.services.chat_service import chat_once

router = APIRouter()


@router.post("")
def chat(payload: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = db.get(ChatSession, payload.session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    model_config = db.get(ModelConfig, payload.model_id)
    if not model_config or model_config.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="模型不存在")
    template = db.get(PromptTemplate, payload.template_id) if payload.template_id else None
    kb = db.get(KnowledgeBase, payload.kb_id) if payload.kb_id else None

    if payload.stream:
        # 让 stream 也走完整工具调用链路：LLM tool_calls -> backend tool execution -> answer。
        answer, turn_count, notice, tools_used = chat_once(
            db, session, model_config, payload.message, template, kb
        )

        async def event_generator():
            # 1) meta（含本轮工具名，便于前端在流式输出前展示）
            yield f"data: {json.dumps({'type': 'meta', 'turn_count': turn_count, 'notice': notice, 'tools_used': tools_used}, ensure_ascii=False)}\n\n"

            # 2) chunk
            # 为了和前端兼容，这里按字符长度粗分段（非 token 级别流式）。
            chunk_size = 120
            for i in range(0, len(answer), chunk_size):
                part = answer[i : i + chunk_size]
                yield f"data: {json.dumps({'type': 'chunk', 'text': part}, ensure_ascii=False)}\n\n"

            # 3) final
            yield f"data: {json.dumps({'type': 'final', 'answer': answer, 'turn_count': turn_count, 'notice': notice, 'tools_used': tools_used}, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    answer, turn_count, notice, tools_used = chat_once(db, session, model_config, payload.message, template, kb)
    data = ChatResponse(answer=answer, turn_count=turn_count, notice=notice, tools_used=tools_used).model_dump()
    if payload.output_mode == "multimodal":
        data["multimodal_placeholder"] = {"image": None}
    return ok(data)
