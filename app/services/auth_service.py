"""认证业务逻辑。"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.models.token import RefreshToken
from app.models.user import User


def register_user(db: Session, username: str, email: str, password: str) -> User:
    user = User(username=username, email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def issue_tokens(db: Session, user: User) -> tuple[str, str]:
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.add(RefreshToken(user_id=user.id, token=refresh_token, expires_at=expires_at))
    db.commit()
    return access_token, refresh_token
