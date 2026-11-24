"""种子数据：RBAC 的角色与权限点。

职责边界：迁移只管表结构，这个脚本只管表里的数据。
可重复执行：每一条都先按 code 查，已存在就跳过，不会插重复行。

为什么权限点也放在脚本里、而不是塞进迁移：迁移里插数据，回滚时要连数据一起撤，
改一个权限名就得再写一份迁移；放在幂等脚本里，改完重跑一次就同步了。

用法（在项目根目录执行）：
    uv run python scripts/seed_rbac.py
"""

import sys
from pathlib import Path

# 脚本是"以文件方式"直接运行的，项目根目录不在 sys.path 里，直接 import app 会失败。
# 这一行把项目根目录补进去，和 scripts/create_admin.py 同一个处理
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.rbac import Permission, Role, RolePermission  # noqa: E402

logger = get_logger(__name__)

# 权限点清单：code 必须符合"资源:动作、全小写、资源复数"。
# 改这里的 code 等于改代码契约，所有 require_permission("...") 要同步改，否则接口永远 403
PERMISSION_SEEDS = [
    ("users:create", "新建用户"),
    ("users:disable", "停用用户"),
    ("users:list", "查看用户列表"),
    ("users:assign_role", "授予或撤销用户角色"),
]

# 角色清单：第四个元素是该角色包含的权限码，空列表表示这个角色没有任何权限点
ROLE_SEEDS = [
    (
        "admin", "管理员", "拥有全部管理权限",
        ["users:create", "users:disable", "users:list", "users:assign_role"],
    ),
    ("user", "普通用户", "登录后只能查看自己的信息", []),
]


# 做什么：确保一个权限点存在，不存在则新建
# 入参：db 会话；code 权限码；name 显示名
# 返回：该权限点的 ORM 对象（新建或已有都返回）
def ensure_permission(db: Session, code: str, name: str) -> Permission:
    existing = db.scalars(select(Permission).where(Permission.code == code)).one_or_none()
    if existing is not None:
        return existing

    permission = Permission(code=code, name=name)
    db.add(permission)
    # flush 而不是 commit：先拿到数据库生成的 id，后面挂角色关系要用；
    # 整个脚本最后统一 commit 一次，中途失败就一条都不落库
    db.flush()
    logger.info("新增权限点 %s（%s）", code, name)
    return permission


# 做什么：确保一个角色存在，不存在则新建
# 入参：db 会话；code 角色标识；name 显示名；description 说明
# 返回：该角色的 ORM 对象（新建或已有都返回）
def ensure_role(db: Session, code: str, name: str, description: str) -> Role:
    existing = db.scalars(select(Role).where(Role.code == code)).one_or_none()
    if existing is not None:
        return existing

    role = Role(code=code, name=name, description=description)
    db.add(role)
    db.flush()
    logger.info("新增角色 %s（%s）", code, name)
    return role


# 做什么：给一个角色挂上一组权限点，已经挂过的跳过
# 入参：db 会话；role 角色对象；permissions 该角色应拥有的权限点列表
# 返回：无（副作用是往 role_permissions 补缺失的行）
# 为什么逐条查而不是先删后插：删了再插会把绑定时间重置，排查问题时看不出原来什么时候授的
def link_role_permissions(db: Session, role: Role, permissions: list[Permission]) -> None:
    for permission in permissions:
        existing = db.scalars(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id,
            )
        ).one_or_none()
        if existing is not None:
            continue

        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        logger.info("角色 %s 挂上权限 %s", role.code, permission.code)


# 做什么：把某张表当前的数据打印出来，方便肉眼核对种子有没有落上
# 入参：db 会话
# 返回：无（只往终端打印）
def print_summary(db: Session) -> None:
    for role in db.scalars(select(Role).order_by(Role.id)).all():
        permission_codes = db.scalars(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
            .order_by(Permission.code)
        ).all()
        text = ", ".join(permission_codes) if permission_codes else "无权限"
        print(f"{role.code}（{role.name}）: {text}")


# 做什么：脚本入口，造齐权限点和角色，最后统一提交
# 入参：无
# 返回：无（退出码 0 表示成功）
def main() -> None:
    setup_logging("dev")

    with SessionLocal() as db:
        permissions = {
            code: ensure_permission(db, code, name) for code, name in PERMISSION_SEEDS
        }

        for code, name, description, permission_codes in ROLE_SEEDS:
            role = ensure_role(db, code, name, description)
            link_role_permissions(db, role, [permissions[c] for c in permission_codes])

        db.commit()
        print_summary(db)


if __name__ == "__main__":
    main()
