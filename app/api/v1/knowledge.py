"""登录用户的知识库与文档接口。"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.schemas.auth import UserInfo
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseListQuery,
    KnowledgeChunkSearchRequest,
    KnowledgeDocumentCreate,
)
from app.services import knowledge as knowledge_service
from app.services.knowledge_upload import upload_knowledge_document
from app.utils.response import success

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])
chunk_router = APIRouter(prefix="/knowledge-chunks", tags=["知识库"])
FILE_UPLOAD_REQUEST = {"requestBody": {"required": True, "content": {
    "multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}},
    }},
}}}


# 做什么：按切片编号返回本人引用所指的原文片段
# 入参：路径中的切片编号、已登录用户及请求级数据库会话
# 返回：统一响应；未登录或切片不可见时返回对应错误
@chunk_router.get("/{chunk_id}", summary="查看本人知识库切片原文")
def get_knowledge_chunk(
    chunk_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    detail = knowledge_service.get_knowledge_chunk(db_session, current_user.id, chunk_id)
    return success(detail.model_dump(mode="json"))


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


# 做什么：接收纯文本文档并返回创建结果
# 入参：知识库编号、已校验的请求体、当前用户和独立写入会话
# 返回：统一响应；未登录、库不可见或参数错误时返回对应错误
@router.post("/{knowledge_base_id}/documents", summary="上传纯文本文档")
def create_knowledge_document(
    knowledge_base_id: int,
    payload: KnowledgeDocumentCreate,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    document = knowledge_service.create_knowledge_document(
        db_session, current_user.id, knowledge_base_id, payload
    )
    return success(document.model_dump(mode="json"))


# 做什么：接收文件流并交给知识库上传服务
# 入参：库编号、上传请求、已登录用户和独立写入会话
# 返回：与纯文本上传一致的文档；归属或文件无效时返回业务错误
@router.post("/{knowledge_base_id}/documents/file", summary="上传知识库文件", openapi_extra=FILE_UPLOAD_REQUEST)
async def create_knowledge_document_from_file(
    knowledge_base_id: int,
    request: Request,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    document = await upload_knowledge_document(
        db_session, current_user.id, knowledge_base_id, request.headers.get("content-type", ""), request.stream(),
    )
    return success(document.model_dump(mode="json"))


# 做什么：接收分页参数并返回指定知识库的文档列表
# 入参：知识库编号、已校验的分页参数、当前用户和请求级数据库会话
# 返回：统一分页响应；未登录、库不可见或参数错误时返回对应错误
@router.get("/{knowledge_base_id}/documents", summary="知识库文档列表")
def list_knowledge_documents(
    knowledge_base_id: int,
    query: KnowledgeBaseListQuery = Depends(),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    documents = knowledge_service.list_knowledge_documents(
        db_session, current_user.id, knowledge_base_id, query
    )
    return success(documents.model_dump(mode="json"))


# 做什么：接收知识库搜索请求并返回命中的切片
# 入参：知识库编号、已校验的查询请求、当前用户和数据库会话
# 返回：统一响应；未登录、知识库不可见或参数不合法时返回对应错误
@router.post("/{knowledge_base_id}/search", summary="检索本人知识库切片")
def search_knowledge_chunks(
    knowledge_base_id: int,
    payload: KnowledgeChunkSearchRequest,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    matches = knowledge_service.search_knowledge_chunks(
        db_session, current_user.id, knowledge_base_id, payload
    )
    return success(matches.model_dump(mode="json"))


# 做什么：删除当前用户指定的知识库
# 入参：知识库编号、已登录用户和独立写入会话
# 返回：统一响应及删除计数；未登录或库不可见时返回对应错误
@router.delete("/{knowledge_base_id}", summary="删除个人知识库")
def delete_knowledge_base(
    knowledge_base_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    result = knowledge_service.delete_knowledge_base(db_session, current_user.id, knowledge_base_id)
    return success(result.model_dump(mode="json"))


# 做什么：删除当前用户指定的知识库文档
# 入参：知识库编号、文档编号、已登录用户和独立写入会话
# 返回：统一响应及删除切片数；库或文档不可见时返回对应错误
@router.delete("/{knowledge_base_id}/documents/{document_id}", summary="删除知识库文档")
def delete_knowledge_document(
    knowledge_base_id: int,
    document_id: int,
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db, use_cache=False),
) -> dict:
    result = knowledge_service.delete_knowledge_document(db_session, current_user.id, knowledge_base_id, document_id)
    return success(result.model_dump(mode="json"))


# 做什么：分页返回知识库文档的原文切片
# 入参：库编号、文档编号、分页参数、已登录用户和数据库会话
# 返回：统一分页响应；库或文档不可见时返回对应错误
@router.get("/{knowledge_base_id}/documents/{document_id}/chunks", summary="分页查看文档原文切片")
def list_knowledge_document_chunks(
    knowledge_base_id: int,
    document_id: int,
    query: KnowledgeBaseListQuery = Depends(),
    current_user: UserInfo = Depends(get_current_user),
    db_session: Session = Depends(get_db),
) -> dict:
    chunks = knowledge_service.list_knowledge_document_chunks(
        db_session, current_user.id, knowledge_base_id, document_id, query,
    )
    return success(chunks.model_dump(mode="json"))
