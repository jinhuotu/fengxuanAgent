"""新增 user_builtin_tool_prefs 表，用于内置工具开关。

Revision ID: 20260509_0003
Revises: 20260429_0002
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260509_0003"
down_revision = "20260429_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_builtin_tool_prefs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tool_key", sa.String(length=128), nullable=False, index=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "tool_key", name="uq_user_builtin_tool"),
    )


def downgrade() -> None:
    op.drop_table("user_builtin_tool_prefs")
