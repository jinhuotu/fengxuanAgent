"""登录用户的建库与本库列表接口。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.knowledge import KnowledgeBaseCreate, KnowledgeBaseListQuery
from app.services import knowledge as knowledge_service
from app.utils.response import success

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


# 做什么：接收建库请求并返回新库的公开字段
# 入参：已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；未登录返回 401，参数错误返回 422
@router.post("", summary="新建个人知识库")
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    current_user: UserInfo = Depends(get_current_user),
    # 新手注意：鉴权查询已经用了自己的草稿纸，另取一张给建库事务使用。
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    knowledge_base = knowledge_service.create_knowledge_base(db_session, current_user.id, payload)
    return success(knowledge_base.model_dump(mode="json"))


# 做什么：接收分页参数并返回当前用户的知识库
# 入参：已校验的分页参数、当前用户和请求级数据库会话
# 返回：统一分页响应；未登录返回 401，参数错误返回 422
@router.get("", summary="本库列表")
def list_knowledge_bases(
    query: KnowledgeBaseListQuery = Depends(),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    knowledge_bases = knowledge_service.list_knowledge_bases(db_session, current_user.id, query)
    return success(knowledge_bases.model_dump(mode="json"))
