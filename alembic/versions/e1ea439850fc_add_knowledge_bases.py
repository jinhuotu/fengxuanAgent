"""新增个人知识库表。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e1ea439850fc"
down_revision = "9c4d16b8e2f1"
branch_labels = None
depends_on = None


# 做什么：创建知识库表及本人列表使用的索引
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True, nullable=False, comment="知识库编号，自增",
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment="归属用户编号，不建物理外键"),
        sa.Column("name", sa.String(length=50), nullable=False, comment="知识库名称"),
        sa.Column("description", sa.String(length=200), nullable=True, comment="知识库说明，可为空"),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False, comment="创建时间，东八区",
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"),
            nullable=False, comment="更新时间，东八区",
        ),
        sa.Column(
            "is_deleted", mysql.TINYINT().with_variant(sa.Integer(), "sqlite"),
            server_default="0", nullable=False, comment="软删除标记，零表示正常，一表示删除",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
        comment="知识库：用户自建的资料集合，后续挂文档与切片",
    )
    op.create_index(
        "idx_knowledge_bases_owner_id", "knowledge_bases", ["owner_id", "is_deleted", "id"],
        unique=False,
    )


# 做什么：回退本次迁移，移除知识库表及其索引
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_index("idx_knowledge_bases_owner_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
