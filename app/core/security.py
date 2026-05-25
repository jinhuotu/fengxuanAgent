"""密码哈希与 JWT 等安全相关工具。"""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt
from passlib.context import CryptContext
from app.core.config import get_settings

# 默认使用 pbkdf2_sha256，避免 passlib 与 bcrypt 后端兼容性问题。
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    # 兼容数据库中已存在的旧版 bcrypt 哈希。
    if hashed_password.startswith("$2a$") or hashed_password.startswith("$2b$") or hashed_password.startswith("$2y$"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
        except ValueError:
            return False
    return pwd_context.verify(password, hashed_password)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {"sub": subject, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
