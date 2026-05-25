"""新增 MCP Server 与工具相关表。

Revision ID: 20260429_0002
Revises: 20260429_0001
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa

revision = "20260429_0002"
down_revision = "20260429_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("server_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=512), nullable=False),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("env_json", sa.JSON(), nullable=False),
        sa.Column("working_dir", sa.String(length=1024), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("startup_timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("restart_policy", sa.String(length=32), nullable=False, server_default="restart"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "mcp_tools",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "server_config_id",
            sa.Integer(),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tool_name", sa.String(length=255), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "mcp_tool_call_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "server_config_id",
            sa.Integer(),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("chat_session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mcp_tool_call_sessions")
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")

