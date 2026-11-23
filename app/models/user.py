"""用户表。

阶段一只需要"能登录、能被管理员管"，所以字段只保留登录和账号管理必需的列。
角色相关的列故意不加：权限在阶段二用独立的角色表和权限表做，
现在往 users 里塞 role 列，到阶段二一定要拆表，等于给自己留债。

每个列都带 comment：这份文案会写进 DDL，存进 MySQL，
给不打开代码、只看数据库的人看（运维、BI、半年后的自己）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 状态用常量不用魔法字符串：别处判断"是否停用"时引用常量，拼错了会立刻报错而不是悄悄失效
USER_STATUS_ACTIVE = "active"
USER_STATUS_DISABLED = "disabled"


class User(Base):
    __tablename__ = "users"
    # 表级注释：数据库客户端的表列表里能直接看到这张表是干什么的
    __table_args__ = {"comment": "用户表：登录账号、账号状态与是否管理员"}

    # 主键：自增整数。阶段一单表，够用且一眼看得懂
    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键，自增"
    )

    # 登录名：唯一索引，一个人只能有一个账号名，查重和登录查询都靠它
    username: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="登录名，全局唯一"
    )

    # 密码只存 bcrypt 哈希，永不存明文。长度 100：bcrypt 固定 60，余量留给以后换算法
    password_hash: Mapped[str] = mapped_column(
        String(100), comment="密码哈希（bcrypt），永不存明文"
    )

    # 显示名：可空。不填就让前端退化成显示用户名，不必强制填
    display_name: Mapped[str | None] = mapped_column(
        String(50), default=None, comment="显示名，可为空，为空时前端显示登录名"
    )

    # 账号状态：active 正常、disabled 被管理员停用（登录时返回错误码 1003）
    status: Mapped[str] = mapped_column(
        String(20),
        default=USER_STATUS_ACTIVE,
        comment="账号状态：active 正常，disabled 已停用",
    )

    # 是否管理员：阶段一靠这一列决定"谁能建用户"。
    # 阶段二会引入角色表，到时把它迁成"用户挂角色"，这一列删除
    # server_default 写在数据库侧：老数据补列时不会留空值
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
        comment="是否管理员：1 是，0 否",
    )

    # 时间交给数据库生成，不用 Python 的 datetime.now()：
    # 应用服务器和数据库时区可能不一致，两边各写一遍时间以后一定对不上
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，由数据库生成"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间，由数据库生成",
    )
