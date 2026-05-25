"""提示词模板 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.prompt import PromptTemplate
from app.models.user import User
from app.schemas.prompt import PromptTemplateCreateRequest, PromptTemplateUpdateRequest
from app.schemas.response import ok
from app.services.prompt_service import create_prompt

router = APIRouter()


@router.post("")
def create_prompt_template(
    payload: PromptTemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = create_prompt(db, current_user.id, payload.name, payload.template, payload.tags)
    return ok({"id": item.id})


@router.get("")
def list_prompts(
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(PromptTemplate).filter(PromptTemplate.user_id == current_user.id)
    if q:
        query = query.filter(PromptTemplate.name.like(f"%{q}%"))
    rows = query.order_by(PromptTemplate.updated_at.desc()).all()
    return ok([{"id": r.id, "name": r.name, "tags": r.tags, "template": r.template} for r in rows])


@router.put("/{prompt_id}")
def update_prompt_template(
    prompt_id: int,
    payload: PromptTemplateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    item = db.get(PromptTemplate, prompt_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="模板不存在")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(item, k, v)
    db.commit()
    return ok({"id": item.id})


@router.delete("/{prompt_id}")
def delete_prompt_template(prompt_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = db.get(PromptTemplate, prompt_id)
    if not item or item.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(item)
    db.commit()
    return ok(message="deleted")
