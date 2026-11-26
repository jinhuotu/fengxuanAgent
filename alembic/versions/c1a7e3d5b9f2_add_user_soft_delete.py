"""为用户表增加软删除标记。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "c1a7e3d5b9f2"
down_revision = "d7f4e2a91c63"
branch_labels = None
depends_on = None


# 做什么：给 users 增加软删除标记列，已有用户全部按"未删除"处理
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_deleted",
            mysql.TINYINT().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
            server_default=sa.text("0"),
            comment="软删除标记，零表示正常，一表示已删除",
        ),
    )


# 做什么：在明确回退本次迁移时移除软删除标记列
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_column("users", "is_deleted")
