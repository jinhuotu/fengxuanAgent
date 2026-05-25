"""认证 API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.schemas.response import ok
from app.services.auth_service import authenticate, issue_tokens, register_user

router = APIRouter()


@router.post("/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    user = register_user(db, payload.username, payload.email, payload.password)
    return ok({"id": user.id, "username": user.username})


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    access_token, refresh_token = issue_tokens(db, user)
    return ok(TokenResponse(access_token=access_token, refresh_token=refresh_token).model_dump())


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    token_row = db.query(RefreshToken).filter(RefreshToken.token == payload.refresh_token).first()
    if not token_row:
        raise HTTPException(status_code=401, detail="无效刷新令牌")
    user = db.get(User, token_row.user_id)
    access_token, refresh_token = issue_tokens(db, user)
    return ok(TokenResponse(access_token=access_token, refresh_token=refresh_token).model_dump())


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return ok({"id": current_user.id, "username": current_user.username, "email": current_user.email})
