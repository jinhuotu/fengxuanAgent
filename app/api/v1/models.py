"""模型管理 API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.model_config import ModelConfig
from app.models.user import User
from app.schemas.model_config import ModelCreateRequest, ModelUpdateRequest
from app.schemas.response import ok
from app.services.model_service import create_model_config
from app.utils.crypto import encrypt_text

router = APIRouter()


@router.post("")
def create_model(
    payload: ModelCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    model = create_model_config(db, current_user.id, payload.model_dump())
    return ok({"id": model.id})


@router.get("")
def list_models(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    models = db.query(ModelConfig).filter(ModelConfig.user_id == current_user.id).all()
    return ok([{"id": m.id, "model_type": m.model_type, "model_name": m.model_name, "base_url": m.base_url} for m in models])


@router.put("/{model_id}")
def update_model(
    model_id: int, payload: ModelUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    model = db.get(ModelConfig, model_id)
    if not model or model.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="模型不存在")
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        if k == "api_key":
            model.api_key_encrypted = encrypt_text(v)
        else:
            setattr(model, k, v)
    db.commit()
    return ok({"id": model.id})


@router.delete("/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    model = db.get(ModelConfig, model_id)
    if not model or model.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="模型不存在")
    db.delete(model)
    db.commit()
    return ok(message="deleted")
