"""重建 Qdrant 向量索引：删除旧集合后按数据库中有效切片重新向量化。"""
import sys
from pathlib import Path

from sqlalchemy import select

# 脚本以文件方式运行，项目根目录不在 sys.path 里，先补进去。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.knowledge import IS_NOT_DELETED, KnowledgeDocument  # noqa: E402
from app.rag import vector_index  # noqa: E402


# 做什么：检查配置后重建整个集合，并逐文档写入有效切片
# 入参：无，读 .env 中的 Qdrant 与模型配置
# 返回：进程退出码；未启用或写入失败返回非零
def main() -> int:
    settings = get_settings()
    if not settings.vector_enabled:
        print("VECTOR_ENABLED=false，请先在 .env 启用向量检索")
        return 2
    client = vector_index.get_client()
    print(f"Qdrant {settings.qdrant_url} 集合 {settings.qdrant_collection} 开始重建")
    if client.collection_exists(settings.qdrant_collection):
        client.delete_collection(settings.qdrant_collection)
    vector_index.ensure_collection(client)
    written = 0
    with SessionLocal() as db_session:
        documents = db_session.execute(select(
            KnowledgeDocument.id, KnowledgeDocument.knowledge_base_id, KnowledgeDocument.owner_id,
        ).where(KnowledgeDocument.is_deleted == IS_NOT_DELETED).order_by(KnowledgeDocument.id)).all()
        for document_id, knowledge_base_id, owner_id in documents:
            written += vector_index.sync_document(db_session, knowledge_base_id, owner_id, document_id, client)
    print(f"完成：文档 {len(documents)} 篇，向量点 {written} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
