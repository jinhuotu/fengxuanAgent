"""保存每轮助手消息的知识库引用快照。"""

from alembic import op
import sqlalchemy as sa


revision = "d7f4e2a91c63"
down_revision = "af4cfc254421"
branch_labels = None
depends_on = None


# 做什么：为消息表添加可为空的引用快照列
# 入参：无，使用 Alembic 当前连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "references_json", sa.Text(), nullable=True,
            comment="本轮引用的 JSON 快照，空表示未使用知识库或引用为空",
        ),
    )


# 做什么：回退引用快照列
# 入参：无，使用 Alembic 当前连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_column("chat_messages", "references_json")
