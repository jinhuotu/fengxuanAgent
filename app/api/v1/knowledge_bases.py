"""知识库 API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.knowledge_base import KBCreateRequest, KBIngestRequest, KBUpdateRequest
from app.schemas.response import ok
from app.services.knowledge_base_service import create_kb, delete_kb, ingest_kb_text

router = APIRouter()


@router.post("")
def create_knowledge_base(
    payload: KBCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    kb = create_kb(db, current_user.id, payload.name, payload.description)
    return ok({"id": kb.id, "collection": kb.chroma_collection})


@router.get("")
def list_knowledge_bases(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == current_user.id).all()
    return ok([{"id": r.id, "name": r.name, "description": r.description} for r in rows])


@router.put("/{kb_id}")
def update_knowledge_base(
    kb_id: int, payload: KBUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or kb.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="知识库不存在")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(kb, k, v)
    db.commit()
    return ok({"id": kb.id})


@router.delete("/{kb_id}")
def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or kb.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="知识库不存在")
    delete_kb(db, kb)
    return ok(message="deleted")


@router.post("/{kb_id}/ingest")
def ingest_knowledge(
    kb_id: int, payload: KBIngestRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    kb = db.get(KnowledgeBase, kb_id)
    if not kb or kb.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="知识库不存在")
    doc = ingest_kb_text(db, kb, payload.source_name, payload.content)
    return ok({"id": doc.id})
