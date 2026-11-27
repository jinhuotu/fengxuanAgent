"""知识库文档补充来源、文件类型、文件名与文件大小四列。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f9c4d2b81e5a"
down_revision = "e8b3c6a12d47"
branch_labels = None
depends_on = None

FLAG_TYPE = mysql.TINYINT().with_variant(sa.Integer(), "sqlite")
FILE_NAME_LENGTH = 200


# 做什么：给知识库文档加上来源与文件元数据四列
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；加列失败由 Alembic 报错
def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column(
        "source_type", FLAG_TYPE, nullable=False, server_default=sa.text("2"),
        comment="来源：一上传文件，二粘贴文本，三URL抓取",
    ))
    op.add_column("knowledge_documents", sa.Column(
        "file_type", FLAG_TYPE, nullable=False, server_default=sa.text("1"),
        comment="文件类型：一文本，二PDF，三Word，四PPT，五Excel，六图片，七视频",
    ))
    op.add_column("knowledge_documents", sa.Column(
        "file_name", sa.String(FILE_NAME_LENGTH), comment="原始文件名，粘贴文本为空",
    ))
    op.add_column("knowledge_documents", sa.Column(
        "file_size", sa.Integer(), comment="原始文件字节数，粘贴文本为空",
    ))


# 做什么：回滚时按相反顺序删除这四列
# 入参：无，使用 Alembic 当前迁移连接
# 返回：无；删列失败由 Alembic 报错
def downgrade() -> None:
    op.drop_column("knowledge_documents", "file_size")
    op.drop_column("knowledge_documents", "file_name")
    op.drop_column("knowledge_documents", "file_type")
    op.drop_column("knowledge_documents", "source_type")
