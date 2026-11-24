"""新增知识库切片和 MySQL 全文索引。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e433f3124db0"
down_revision = "f12630e761c9"
branch_labels = None
depends_on = None

CHUNK_TABLE = "knowledge_chunks"
FULLTEXT_INDEX = "ft_knowledge_chunks_content"


# 做什么：创建切片表，记录原文偏移、归属和软删除状态
# 入参：无，使用 Alembic 当前连接
# 返回：无；建表失败由 Alembic 报错
def _create_chunk_table() -> None:
    identifier_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    content_type = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
    deletion_type = mysql.TINYINT().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        CHUNK_TABLE,
        sa.Column("id", identifier_type, autoincrement=True, nullable=False, comment="切片编号，自增"),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=False, comment="所属知识库编号"),
        sa.Column("document_id", sa.BigInteger(), nullable=False, comment="所属文档编号"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment="归属用户编号，用于过滤"),
        sa.Column("chunk_index", sa.Integer(), nullable=False, comment="文档内切片序号，从零开始"),
        sa.Column("heading_path", sa.String(length=120), nullable=False, comment="标题与段落路径"),
        sa.Column("content", content_type, nullable=False, comment="切片正文"),
        sa.Column("char_start", sa.Integer(), nullable=False, comment="原文起始字符偏移，包含该位置"),
        sa.Column("char_end", sa.Integer(), nullable=False, comment="原文结束字符偏移，不包含该位置"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="更新时间，东八区"),
        sa.Column(
            "is_deleted", deletion_type, server_default="0", nullable=False,
            comment="软删除标记，零表示正常，一表示删除",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        comment="知识库切片：保存文档片段与在原文中的字符位置",
    )


# 做什么：添加切片表、普通索引及仅 MySQL 使用的全文索引
# 入参：无，使用 Alembic 当前连接
# 返回：无；表或索引创建失败由 Alembic 报错
def upgrade() -> None:
    _create_chunk_table()
    op.create_index("idx_knowledge_chunks_base", CHUNK_TABLE, ["knowledge_base_id", "is_deleted", "id"])
    op.create_index("idx_knowledge_chunks_doc", CHUNK_TABLE, ["document_id", "is_deleted", "chunk_index"])
    # 新手注意：ngram 像用滑动窗口给原文制卡，SQLite 测试不需要这套索引。
    if op.get_bind().dialect.name == "mysql":
        op.create_index(
            FULLTEXT_INDEX, CHUNK_TABLE, ["content"],
            mysql_prefix="FULLTEXT", mysql_with_parser="ngram",
        )


# 做什么：回退本次新增的切片索引和表
# 入参：无，使用 Alembic 当前连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.drop_index(FULLTEXT_INDEX, table_name=CHUNK_TABLE)
    op.drop_index("idx_knowledge_chunks_doc", table_name=CHUNK_TABLE)
    op.drop_index("idx_knowledge_chunks_base", table_name=CHUNK_TABLE)
    op.drop_table(CHUNK_TABLE)
