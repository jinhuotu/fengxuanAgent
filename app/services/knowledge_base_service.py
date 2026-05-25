"""知识库服务（关联 Chroma）。"""

from sqlalchemy.orm import Session
from app.integrations import vector_store
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument


def create_kb(db: Session, user_id: int, name: str, description: str | None) -> KnowledgeBase:
    collection_name = f"user_{user_id}_kb_{name}".replace(" ", "_").lower()
    vector_store.create_collection(collection_name)
    kb = KnowledgeBase(user_id=user_id, name=name, description=description, chroma_collection=collection_name)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def delete_kb(db: Session, kb: KnowledgeBase) -> None:
    vector_store.delete_collection(kb.chroma_collection)
    db.delete(kb)
    db.commit()


def ingest_kb_text(db: Session, kb: KnowledgeBase, source_name: str, content: str) -> KnowledgeDocument:
    vector_store.ingest_text(kb.chroma_collection, source_name, content)
    doc = KnowledgeDocument(kb_id=kb.id, source_name=source_name, content=content)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def retrieve_context(kb: KnowledgeBase, query: str) -> str:
    texts = vector_store.retrieve(kb.chroma_collection, query, top_k=4)
    return "\n".join(texts)
