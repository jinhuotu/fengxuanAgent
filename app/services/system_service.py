"""系统状态巡检服务。"""

from importlib.metadata import PackageNotFoundError, version

import langchain
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.integrations.vector_store import client
from app.models.model_config import ModelConfig


def _safe_package_version(package_name: str, fallback_attr_module=None) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        if fallback_attr_module is not None:
            return getattr(fallback_attr_module, "__version__", "unknown")
        return "unknown"


def get_system_status(db: Session) -> dict:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    chroma_ok = True
    try:
        client.heartbeat()
    except Exception:
        chroma_ok = False
    models = db.query(ModelConfig).all()
    return {
        "langchain_version": _safe_package_version("langchain", langchain),
        "langgraph_version": _safe_package_version("langgraph"),
        "mysql_status": db_ok,
        "chroma_status": chroma_ok,
        "loaded_models": [{"id": m.id, "name": m.model_name, "type": m.model_type} for m in models],
    }


def get_runtime_snapshot() -> dict:
    """面向运维/学习场景的非敏感配置快照。"""
    s = get_settings()
    return {
        "app_name": s.app_name,
        "app_env": s.app_env,
        "app_port": s.app_port,
        "app_debug": s.app_debug,
    }
