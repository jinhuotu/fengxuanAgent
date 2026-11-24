"""新增会话级知识库选择。"""

from alembic import op
import sqlalchemy as sa


revision = "af4cfc254421"
down_revision = "e433f3124db0"
branch_labels = None
depends_on = None


# 做什么：添加会话可选的知识库编号，存量会话默认不选库
# 入参：无，使用 Alembic 当前连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "knowledge_base_id", sa.BigInteger(), nullable=True,
            comment="所选知识库编号，为空表示本次会话不使用知识库",
        ),
    )


# 做什么：回退会话级选库列，保留原有会话字段
# 入参：无，使用 Alembic 当前连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_column("chat_sessions", "knowledge_base_id")
