"""角色绑定和撤销业务；调用入口负责先验证操作者权限。"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    CannotModifySelfError,
    LastAdminProtectedError,
    PermissionDeniedError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import IS_NOT_DELETED, USER_STATUS_ACTIVE, User
from app.schemas.rbac import RoleSummary

logger = logging.getLogger(__name__)
PERMISSION_USERS_CREATE = "users:create"
PERMISSION_USERS_LIST = "users:list"
PERMISSION_USERS_ASSIGN_ROLE = "users:assign_role"
PERMISSION_USERS_DISABLE = "users:disable"
PERMISSION_USERS_DELETE = "users:delete"
ROLE_LIST_BATCH_SIZE = 100
# 与 scripts/seed_rbac.py 中 admin 角色的代码耦合，变更时需同步。
ADMIN_ROLE_CODE = "admin"


# 做什么：给用户新增一个角色，保留其他角色，重复绑定不新增
# 入参：未开启事务的会话、目标用户编号、角色标识和操作者编号
# 返回：新增 True，已绑定 False；目标不存在或操作本人时抛业务异常
def bind_user_role(db_session: Session, user_id: int, role_code: str, operator_id: int) -> bool:
    if operator_id == user_id:
        raise CannotModifySelfError()
    with db_session.begin():
        # 新手注意：锁住用户行像先拿号码牌，同一用户的绑定请求依次处理。
        user_query = select(User.id).where(User.id == user_id, User.is_deleted == IS_NOT_DELETED).with_for_update()
        if db_session.scalar(user_query) is None:
            raise UserNotFoundError()
        role_id = db_session.scalar(select(Role.id).where(Role.code == role_code))
        if role_id is None:
            raise RoleNotFoundError()
        binding_query = select(UserRole.id).where(
            UserRole.user_id == user_id, UserRole.role_id == role_id
        )
        if db_session.scalar(binding_query) is not None:
            return False
        db_session.add(UserRole(user_id=user_id, role_id=role_id))
    logger.info("角色绑定成功 | operator_id=%s | user_id=%s | role_id=%s", operator_id, user_id, role_id)
    return True


# 做什么：统计指定角色的启用用户数，排除本次操作的目标账号
# 入参：数据库会话、角色代码及要排除的用户编号
# 返回：满足条件的用户数量；数据库错误直接抛出
def count_active_role_holders(db_session: Session, role_code: str, exclude_user_id: int) -> int:
    holder_count_query = (
        select(func.count()).select_from(UserRole)
        .join(User, User.id == UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.code == role_code,
            User.status == USER_STATUS_ACTIVE,
            # 已逻辑删除的账号虽然状态可能还是"正常"，但不算还在岗的管理员
            User.is_deleted == IS_NOT_DELETED,
            UserRole.user_id != exclude_user_id,
        )
    )
    return db_session.scalar(holder_count_query) or 0


# 做什么：撤销目标用户的一条角色绑定，并保护最后一名启用管理员
# 入参：未开启事务的会话、操作者编号、目标用户编号和角色代码
# 返回：撤销 True，未绑定 False；操作本人或移除最后一名管理员时抛业务异常
def unbind_user_role(db_session: Session, operator_id: int, user_id: int, role_code: str) -> bool:
    if operator_id == user_id:
        raise CannotModifySelfError()
    with db_session.begin():
        # 先锁角色，再查剩余人数，避免并发撤销不同账号的 admin 时都通过检查。
        role_query = select(Role.id, (Role.code == ADMIN_ROLE_CODE).label("is_admin_role"))
        locked_role = db_session.execute(role_query.where(Role.code == role_code).with_for_update()).one_or_none()
        if locked_role is None:
            return False
        binding_query = select(UserRole).where(
            UserRole.user_id == user_id, UserRole.role_id == locked_role.id
        )
        binding = db_session.scalar(binding_query.with_for_update())
        if binding is None:
            return False
        if locked_role.is_admin_role and count_active_role_holders(db_session, ADMIN_ROLE_CODE, user_id) == 0:
            raise LastAdminProtectedError()
        db_session.delete(binding)
    logger.info(
        "角色撤销成功 | operator_id=%s | user_id=%s | role_id=%s",
        operator_id, user_id, locked_role.id,
    )
    return True


# 做什么：按角色代码排序，分批读取完整的角色清单
# 入参：请求级数据库会话
# 返回：只有 code 和 name 的角色摘要；无角色返回空列表，数据库错误抛出
# 新手注意：用上一批最后一个代码做书签，下一批从书签后继续读。
def list_roles(db_session: Session) -> list[RoleSummary]:
    role_items: list[RoleSummary] = []
    base_query = select(Role.code, Role.name).order_by(Role.code).limit(ROLE_LIST_BATCH_SIZE)
    role_query = base_query
    while True:
        role_rows = db_session.execute(role_query).all()
        page_items = [RoleSummary(code=row.code, name=row.name) for row in role_rows]
        role_items.extend(page_items)
        if len(role_rows) < ROLE_LIST_BATCH_SIZE:
            return role_items
        role_query = base_query.where(Role.code > role_rows[-1].code)


# 做什么：按角色权限的并集判断用户是否拥有指定权限
# 入参：数据库会话、用户编号和权限码
# 返回：命中返回 True；用户停用、不存在或无权限返回 False，数据库错误直接抛出
def has_permission(db_session: Session, user_id: int, permission_code: str) -> bool:
    permission_query = (
        select(UserRole.id)
        .join(User, User.id == UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            User.id == user_id,
            User.status == USER_STATUS_ACTIVE,
            Permission.code == permission_code,
        )
    )
    # 新手注意：EXISTS 像只问“有没有通行证”，多角色重复授予也只回答是或否。
    return bool(db_session.scalar(select(permission_query.exists())))


# 做什么：要求用户持有指定权限，供接口依赖调用
# 入参：数据库会话、用户编号和权限码
# 返回：有权限时正常结束；无权限时抛 PermissionDeniedError，数据库错误直接抛出
def ensure_permission(db_session: Session, user_id: int, permission_code: str) -> None:
    if not has_permission(db_session, user_id, permission_code):
        raise PermissionDeniedError()


# 做什么：查询用户多角色拥有的权限码，去重并排序
# 入参：数据库会话和已确认身份的用户编号
# 返回：权限码列表；无权限或非正常用户返回空列表，数据库错误抛出
def list_permission_codes(db_session: Session, user_id: int) -> list[str]:
    permission_query = (
        select(Permission.code).select_from(UserRole)
        .join(User, User.id == UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(User.id == user_id, User.status == USER_STATUS_ACTIVE)
    )
    # 新手注意：DISTINCT 像把多张角色清单上的相同通行证合成一张。
    permission_query = permission_query.distinct().order_by(Permission.code)
    return list(db_session.scalars(permission_query).all())
