"""增加全站生成准入锁及有效占用查询索引，不改变已有对话正文。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "7e9204b6d815"
down_revision = "24c713a60f20"
branch_labels = None
depends_on = None
GENERATION_GATE_ID = 1


# 做什么：创建固定准入锁记录，并增加有效生成占用索引
# 入参：无，使用 Alembic 当前连接
# 返回：无；迁移失败由命令报错，不操作已有消息
def upgrade() -> None:
    gate_table = op.create_table(
        "chat_generation_gates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="准入锁编号，迁移创建编号一的固定记录"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="更新时间，东八区"),
        sa.Column("is_deleted", mysql.TINYINT(), server_default="0", nullable=False, comment="软删除标记，固定记录必须保持零"),
        comment="对话生成准入锁：固定记录协调多个进程的全站并发检查",
    )
    op.bulk_insert(gate_table, [{"id": GENERATION_GATE_ID}])
    op.create_index("idx_chat_sessions_active_expiry", "chat_sessions", ["is_deleted", "generation_expires_at"])


# 做什么：回退准入锁和本次新增索引，保留会话消息
# 入参：无，使用 Alembic 当前连接
# 返回：无；仅在明确执行回退迁移时生效
def downgrade() -> None:
    op.drop_index("idx_chat_sessions_active_expiry", table_name="chat_sessions")
    op.drop_table("chat_generation_gates")
