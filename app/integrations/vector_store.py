"""Chroma 向量库封装。"""

from chromadb import PersistentClient
from app.core.config import get_settings

settings = get_settings()
client = PersistentClient(path=settings.chroma_persist_directory)


def create_collection(name: str):
    return client.get_or_create_collection(name=name)


def delete_collection(name: str) -> None:
    client.delete_collection(name=name)


def ingest_text(collection_name: str, source_name: str, content: str) -> None:
    coll = client.get_or_create_collection(name=collection_name)
    chunks = [content[i : i + 800] for i in range(0, len(content), 800)] or [content]
    ids = [f"{source_name}-{idx}" for idx, _ in enumerate(chunks)]
    coll.add(ids=ids, documents=chunks, metadatas=[{"source": source_name}] * len(chunks))


def retrieve(collection_name: str, query: str, top_k: int = 4) -> list[str]:
    coll = client.get_or_create_collection(name=collection_name)
    result = coll.query(query_texts=[query], n_results=top_k)
    return result.get("documents", [[]])[0]
