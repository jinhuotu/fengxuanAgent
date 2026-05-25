"""聊天会话管理 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.schemas.chat import SessionCreateRequest
from app.schemas.response import ok

router = APIRouter()


@router.post("")
def create_session(
    payload: SessionCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    session = ChatSession(
        user_id=current_user.id,
        title=payload.title,
        model_config_id=payload.model_config_id,
        client_label=payload.client_label,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return ok({"id": session.id})


@router.get("")
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(ChatSession).filter(ChatSession.user_id == current_user.id).order_by(ChatSession.updated_at.desc()).all()
    return ok(
        [
            {
                "id": s.id,
                "title": s.title,
                "client_label": s.client_label,
                "updated_at": s.updated_at.isoformat(),
            }
            for s in rows
        ]
    )


@router.delete("/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = db.get(ChatSession, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.delete(session)
    db.commit()
    return ok(message="deleted")


@router.get("/{session_id}/messages")
def list_messages(
    session_id: int,
    page: int = Query(default=1, ge=1),
    # 前端「拉满一页历史」常见为 200；原先 le=100 会导致 size=200 返回 422。
    size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.get(ChatSession, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc())
    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()
    data = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "turn_index": m.turn_index,
            "tools_used": m.tools_used_json or [],
        }
        for m in rows
    ]
    return ok({"total": total, "page": page, "size": size, "items": data})
