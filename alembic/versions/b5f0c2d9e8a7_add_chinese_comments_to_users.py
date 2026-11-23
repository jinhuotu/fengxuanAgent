"""add chinese comments to users

Revision ID: b5f0c2d9e8a7
Revises: a743e8871d18
Create Date: 2026-11-20 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b5f0c2d9e8a7'
down_revision: Union[str, Sequence[str], None] = 'a743e8871d18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 这份迁移是手写的，不是 autogenerate 出来的：
# Alembic 的 autogenerate 不比对 comment，只改注释它只会生成一份空迁移。
#
# 为什么不用 op.alter_column 而直接写 ALTER：MySQL 改列注释走 MODIFY COLUMN，
# 会把整列定义重写一遍。op.alter_column 万一漏了 autoincrement、默认值或字符集，
# 列定义就被改坏了。下面的语句是照 SHOW CREATE TABLE 的原定义抄的，只多一个 COMMENT。
def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE users MODIFY COLUMN id int NOT NULL AUTO_INCREMENT COMMENT '主键，自增'")
    op.execute(
        "ALTER TABLE users MODIFY COLUMN username varchar(50) COLLATE utf8mb4_unicode_ci "
        "NOT NULL COMMENT '登录名，全局唯一'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN password_hash varchar(100) COLLATE utf8mb4_unicode_ci "
        "NOT NULL COMMENT '密码哈希（bcrypt），永不存明文'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN display_name varchar(50) COLLATE utf8mb4_unicode_ci "
        "DEFAULT NULL COMMENT '显示名，可为空，为空时前端显示登录名'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN status varchar(20) COLLATE utf8mb4_unicode_ci "
        "NOT NULL COMMENT '账号状态：active 正常，disabled 已停用'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN created_at datetime NOT NULL DEFAULT (now()) "
        "COMMENT '创建时间，由数据库生成'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN updated_at datetime NOT NULL DEFAULT (now()) "
        "COMMENT '更新时间，由数据库生成'"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN is_admin tinyint(1) NOT NULL DEFAULT '0' "
        "COMMENT '是否管理员：1 是，0 否'"
    )
    op.execute("ALTER TABLE users COMMENT = '用户表：登录账号、账号状态与是否管理员'")


def downgrade() -> None:
    """Downgrade schema."""
    # 回滚就是把 COMMENT 去掉，列定义保持不变
    op.execute("ALTER TABLE users MODIFY COLUMN id int NOT NULL AUTO_INCREMENT")
    op.execute(
        "ALTER TABLE users MODIFY COLUMN username varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN password_hash varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN display_name varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN status varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN created_at datetime NOT NULL DEFAULT (now())"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN updated_at datetime NOT NULL DEFAULT (now())"
    )
    op.execute(
        "ALTER TABLE users MODIFY COLUMN is_admin tinyint(1) NOT NULL DEFAULT '0'"
    )
    op.execute("ALTER TABLE users COMMENT = ''")
