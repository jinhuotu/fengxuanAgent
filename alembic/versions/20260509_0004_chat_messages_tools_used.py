"""为 chat_messages 增加 tools_used_json，供助手工具调用追溯展示。

Revision ID: 20260509_0004
Revises: 20260509_0003
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa

revision = "20260509_0004"
down_revision = "20260509_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("tools_used_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "tools_used_json")
