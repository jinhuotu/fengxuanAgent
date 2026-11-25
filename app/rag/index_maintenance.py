"""维护 MySQL 知识库切片的全文索引。"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

MYSQL_DIALECT = "mysql"
OPTIMIZE_KNOWLEDGE_CHUNKS = "OPTIMIZE TABLE knowledge_chunks"
logger = get_logger(__name__)


# 做什么：重建索引，软删除的切片在原索引里仍留有词条
# 入参：已结束业务事务的会话，索引词条可能只停在内存缓存
# 返回：MySQL 确认操作成功才返回 True，跳过或失败时返回 False
def rebuild_fulltext_index(db_session: Session) -> bool:
    try:
        bind = db_session.get_bind()
        if bind.dialect.name != MYSQL_DIALECT:
            return False
        # 新手注意：自动提交连接像单独开的维修通道，不会把业务写入一起提交。
        with bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            results = connection.execute(text(OPTIMIZE_KNOWLEDGE_CHUNKS)).mappings().all()
        # MySQL 也会把失败放在返回行里，不能只依靠异常判断。
        if any(result["Msg_type"] == "status" and result["Msg_text"] == "OK" for result in results):
            return True
        logger.warning("全文索引重建失败 table=knowledge_chunks MySQL 未返回成功状态")
    except Exception as error:
        logger.warning("全文索引重建失败 table=knowledge_chunks error_type=%s", type(error).__name__)
    return False
