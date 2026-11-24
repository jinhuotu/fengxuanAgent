"""个人知识库的创建与分页查询。"""
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.knowledge import IS_NOT_DELETED, KnowledgeBase
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseItem,
    KnowledgeBaseListQuery,
    KnowledgeBaseListResponse,
)


# 做什么：为当前用户创建知识库，并读取数据库生成的编号与创建时间
# 入参：独立写入会话、当前用户编号和已校验的建库参数
# 返回：新库的公开字段；写入失败时回滚并抛出异常
def create_knowledge_base(
    db_session: Session, owner_id: int, payload: KnowledgeBaseCreate
) -> KnowledgeBaseItem:
    with db_session.begin():
        knowledge_base = KnowledgeBase(
            owner_id=owner_id, name=payload.name, description=payload.description
        )
        db_session.add(knowledge_base)
        db_session.flush()
        knowledge_base_item = KnowledgeBaseItem.model_validate(knowledge_base)
    return knowledge_base_item


# 做什么：按编号倒序列出当前用户的未删除知识库
# 入参：请求级会话、当前用户编号和已校验的分页参数
# 返回：知识库条目、总数和分页信息；数据库异常直接抛出
def list_knowledge_bases(
    db_session: Session, owner_id: int, query: KnowledgeBaseListQuery
) -> KnowledgeBaseListResponse:
    owner_filter = and_(
        KnowledgeBase.owner_id == owner_id, KnowledgeBase.is_deleted == IS_NOT_DELETED
    )
    count_query = select(func.count()).select_from(KnowledgeBase).where(owner_filter)
    total = db_session.execute(count_query).scalar_one()
    offset_count = (query.page - 1) * query.page_size
    knowledge_base_items: list[KnowledgeBaseItem] = []
    # 总数已能确认空页，跳过明细查询，也避免把超大偏移量交给数据库。
    if offset_count < total:
        item_query = select(
            KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.description, KnowledgeBase.created_at
        ).where(owner_filter)
        item_query = item_query.order_by(KnowledgeBase.id.desc()).offset(offset_count)
        item_rows = db_session.execute(item_query.limit(query.page_size)).mappings().all()
        knowledge_base_items = [KnowledgeBaseItem.model_validate(item_row) for item_row in item_rows]
    return KnowledgeBaseListResponse(
        items=knowledge_base_items, total=total, page=query.page, page_size=query.page_size
    )
