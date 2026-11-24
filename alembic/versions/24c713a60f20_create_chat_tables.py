"""新增本人对话历史与生成状态，不修改既有用户或权限表。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "24c713a60f20"
down_revision = "01619bd6e169"
branch_labels = None
depends_on = None


# 做什么：为每张新表创建独立的审计列对象
# 入参：无
# 返回：创建时间、更新时间和软删除列，每列带中文注释
def audit_columns() -> list:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="创建时间，东八区"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="更新时间，东八区"),
        sa.Column("is_deleted", mysql.TINYINT(), server_default="0", nullable=False, comment="软删除标记，零表示正常，一表示删除"),
    ]


# 做什么：创建会话表和本人会话排序索引
# 入参：无，使用 Alembic 当前连接
# 返回：无；建表失败由迁移命令报错
def create_sessions() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="会话编号，自增"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="所属用户编号，由服务校验，不建物理外键"),
        sa.Column("title", sa.String(60), nullable=False, comment="会话标题，首次提问后截取问题生成"),
        sa.Column("history_version", sa.Integer(), nullable=False, comment="历史版本号，隔离不同版本的上下文缓存"),
        sa.Column("active_request_id", sa.String(36), nullable=True, comment="正在生成的请求编号，空表示未占用"),
        sa.Column("generation_expires_at", sa.DateTime(), nullable=True, comment="生成占用的到期时间，到期后标记中断"),
        *audit_columns(),
        comment="对话会话：记录用户归属、历史版本和当前生成请求",
    )
    op.create_index("idx_chat_sessions_user_updated", "chat_sessions", ["user_id", "is_deleted", "updated_at", "id"])


# 做什么：创建消息表、请求唯一约束和历史排序索引
# 入参：无，使用 Alembic 当前连接
# 返回：无；建表失败由迁移命令报错
def create_messages() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="消息编号，自增，作为会话内排序依据"),
        sa.Column("session_id", sa.BigInteger(), nullable=False, comment="所属会话编号，由服务校验，不建物理外键"),
        sa.Column("request_id", sa.String(36), nullable=False, comment="客户端请求编号，同一次问题和回答共用编号"),
        sa.Column("role", mysql.TINYINT(), nullable=False, comment="消息角色，一表示用户，二表示助手"),
        sa.Column("content", mysql.MEDIUMTEXT(), nullable=False, comment="消息正文，失败或中断时可保存已生成的部分回答"),
        sa.Column("status", mysql.TINYINT(), nullable=False, comment="消息状态，一生成中，二完成，三失败，四中断"),
        sa.Column("model", sa.String(100), nullable=True, comment="助手回答使用的模型标识，用户消息为空"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True, comment="模型输入令牌数，未知时为空"),
        sa.Column("completion_tokens", sa.Integer(), nullable=True, comment="模型输出令牌数，未知时为空"),
        sa.Column("finish_reason", sa.String(40), nullable=True, comment="模型结束原因，例如正常结束或达到长度上限"),
        sa.Column("error_code", sa.String(40), nullable=True, comment="安全的错误分类，不保存上游原始错误内容"),
        *audit_columns(),
        sa.UniqueConstraint("session_id", "request_id", "role", name="uk_chat_messages_request_role"),
        comment="对话消息：保存问题、回答、用量及完成或中断状态",
    )
    op.create_index("idx_chat_messages_session_id", "chat_messages", ["session_id", "is_deleted", "id"])


# 做什么：按会话再消息的顺序增加两张表
# 入参：无
# 返回：无；不读写既有用户和权限数据
def upgrade() -> None:
    create_sessions()
    create_messages()


# 做什么：提供新增对话表的回退定义，执行会丢失对话历史
# 入参：无
# 返回：无；只定义回退，本次开发不执行真实库回退
def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")

