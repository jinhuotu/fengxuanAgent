"""系统状态 API。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.response import ok
from app.services.system_service import get_runtime_snapshot, get_system_status

router = APIRouter()


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return ok(get_system_status(db))


@router.get("/runtime")
def runtime():
    """暴露安全的运行时配置（不含密钥）。"""
    return ok(get_runtime_snapshot())
