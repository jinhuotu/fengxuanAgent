"""为 chat_sessions 增加可选 client_label，供客户端打标。

Revision ID: 20260513_0005
Revises: 20260509_0004
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision = "20260513_0005"
down_revision = "20260509_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("client_label", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_sessions", "client_label")
