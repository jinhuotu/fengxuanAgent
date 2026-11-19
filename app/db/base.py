"""所有表结构登记的户口本。

alembic 是靠这里的 metadata 看到"项目里到底有哪些表"的。
所以每个模型文件都必须继承 Base，并且在 app/models/__init__.py 里被 import 一次。
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 约束命名规则：索引、唯一约束、外键、主键都按固定格式起名。
# 为什么不能省：不写的话 MySQL 会随机起名（例如 ix_1a2b3c4d），
# 以后想删某个索引或写回滚脚本，你根本拼不出它的名字。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """所有模型的基类。继承它，等于在户口本上登记这一页。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
