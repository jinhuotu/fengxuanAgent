"""RBAC 的两张主表：角色、权限点。

这一层把"人"和"能做的事"隔开：人挂角色，角色打包权限点。
关联表在下面追加（见 UserRole / RolePermission）。

每个列都带 comment：这份文案会进 DDL、存进数据库，给不看代码的人看。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# 做什么：一张角色表，一个角色就是一组权限的打包
# 入参：无（类本身即表结构声明）
# 返回：无（由 ORM 负责映射成 roles 表）
# 说明：code 给程序判定，name 给人看。分开是为了改中文名时不牵动代码里的判定字符串
class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"comment": "角色表：权限的打包，人和权限之间的中间层"}

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键，自增"
    )
    # code 一旦定下就不要改：代码里写死 require_permission 时靠它做稳定标识
    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, comment="角色标识，全小写，程序判定用，如 admin"
    )
    name: Mapped[str] = mapped_column(String(50), comment="角色显示名，给人看，如 管理员")
    description: Mapped[str | None] = mapped_column(
        String(200), default=None, comment="角色说明，可为空"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，由数据库生成"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间，由数据库生成",
    )


# 做什么：一张权限点表，一个可执行动作一条记录
# 入参：无（类本身即表结构声明）
# 返回：无（由 ORM 负责映射成 permissions 表）
# 说明：code 统一格式 资源:动作，全小写、资源用复数，如 users:create / knowledge:read
class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"comment": "权限点表：一个动作一条记录，如 users:create"}

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键，自增"
    )
    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        comment="权限码，格式 资源:动作，全小写，资源用复数",
    )
    name: Mapped[str] = mapped_column(String(50), comment="权限显示名，如 新建用户")
    description: Mapped[str | None] = mapped_column(
        String(200), default=None, comment="权限说明，可为空"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间，由数据库生成"
    )


# 做什么：人和角色的关联表，一条记录表示"这个人挂了这个角色"
# 入参：无（类本身即表结构声明）
# 返回：无（由 ORM 负责映射成 user_roles 表）
# 为什么不用 users.role_id：一个人可以同时是管理员和审核员，单列装不下多个角色
class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        # 联合唯一：同一个人不能把同一个角色挂两遍，否则权限集合会出现重复行
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id"),
        {"comment": "关联表：谁挂了哪个角色（人和角色多对多）"},
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键，自增"
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        comment="用户 id，指向 users.id，用户删了这条跟着删",
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        index=True,
        comment="角色 id，指向 roles.id，角色删了这条跟着删",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="绑定时间，由数据库生成"
    )


# 做什么：角色和权限点的关联表，一条记录表示"这个角色包含这个权限点"
# 入参：无（类本身即表结构声明）
# 返回：无（由 ORM 负责映射成 role_permissions 表）
# 为什么 permission_id 单独建索引：联合唯一索引的最左列是 role_id，
# 按权限反查"谁会这个操作"时用不到它，得给 permission_id 单独一条路
class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_id"),
        {"comment": "关联表：角色包含哪些权限点（角色和权限多对多）"},
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键，自增"
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        comment="角色 id，指向 roles.id，角色删了这条跟着删",
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"),
        index=True,
        comment="权限点 id，指向 permissions.id，权限点删了这条跟着删",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="授权时间，由数据库生成"
    )
