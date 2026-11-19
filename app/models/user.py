"""用户表。

阶段一只需要"能登录、能被管理员管"，所以字段只保留登录和账号管理必需的列。
角色相关的列故意不加：权限在阶段二用独立的角色表和权限表做，
现在往 users 里塞 role 列，到阶段二一定要拆表，等于给自己留债。
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 状态用常量不用魔法字符串：别处判断"是否停用"时引用常量，拼错了会立刻报错而不是悄悄失效
USER_STATUS_ACTIVE = "active"
USER_STATUS_DISABLED = "disabled"


class User(Base):
    __tablename__ = "users"

    # 主键：自增整数。阶段一单表，够用且一眼看得懂
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # 登录名：唯一索引，一个人只能有一个账号名，查重和登录查询都靠它
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    # 密码只存 bcrypt 哈希，永不存明文。长度 100：bcrypt 固定 60，余量留给以后换算法
    password_hash: Mapped[str] = mapped_column(String(100))

    # 显示名：可空。不填就让前端退化成显示用户名，不必强制填
    display_name: Mapped[str | None] = mapped_column(String(50), default=None)

    # 账号状态：active 正常、disabled 被管理员停用（登录时返回错误码 1003）
    status: Mapped[str] = mapped_column(String(20), default=USER_STATUS_ACTIVE)

    # 时间交给数据库生成，不用 Python 的 datetime.now()：
    # 应用服务器和数据库时区可能不一致，两边各写一遍时间以后一定对不上
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
