"""新增个人知识库文档表。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f12630e761c9"
down_revision = "e1ea439850fc"
branch_labels = None
depends_on = None


# 做什么：创建知识库文档表和库内列表索引
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    identifier_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    status_type = mysql.TINYINT().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "knowledge_documents",
        sa.Column("id", identifier_type, autoincrement=True, nullable=False, comment="文档编号，自增"),
        sa.Column("knowledge_base_id", sa.BigInteger(), nullable=False, comment="所属知识库编号，不建物理外键"),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment="归属用户编号，用于过滤"),
        sa.Column("title", sa.String(length=100), nullable=False, comment="文档标题"),
        sa.Column(
            "raw_content", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"),
            nullable=False, comment="未经处理的纯文本原文",
        ),
        sa.Column("char_count", sa.Integer(), nullable=False, comment="原文字符数，包含空白"),
        sa.Column(
            "status", status_type, nullable=False,
            comment="处理状态：一待切分，二解析中，三切分中，四建索引中，五可检索，六处理失败",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="更新时间，东八区"),
        sa.Column("is_deleted", status_type, server_default="0", nullable=False, comment="软删除标记，零表示正常，一表示删除"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        comment="知识库文档：保存原文与处理状态，后续切分与索引",
    )
    op.create_index(
        "idx_knowledge_documents_base_id", "knowledge_documents",
        ["knowledge_base_id", "is_deleted", "id"], unique=False,
    )


# 做什么：移除本次迁移创建的文档索引和表
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_index("idx_knowledge_documents_base_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
