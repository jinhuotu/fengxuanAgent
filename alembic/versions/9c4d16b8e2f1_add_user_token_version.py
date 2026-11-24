"""为用户增加登录令牌版本号。"""

from alembic import op
import sqlalchemy as sa

revision = "9c4d16b8e2f1"
down_revision = "7e9204b6d815"
branch_labels = None
depends_on = None


# 做什么：给 users 增加令牌版本号，已有用户的初始版本为 0
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；迁移失败由 Alembic 报错
def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="登录令牌版本号，用于使已签发的令牌失效",
        ),
    )


# 做什么：在明确回退本次迁移时移除令牌版本列
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；回退失败由 Alembic 报错
def downgrade() -> None:
    op.drop_column("users", "token_version")
