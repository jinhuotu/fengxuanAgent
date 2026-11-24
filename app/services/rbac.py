"""角色绑定业务；调用入口负责先验证操作者权限。"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import PermissionDeniedError, RoleNotFoundError, UserNotFoundError
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.user import USER_STATUS_ACTIVE, User

logger = logging.getLogger(__name__)
PERMISSION_USERS_CREATE = "users:create"
PERMISSION_USERS_LIST = "users:list"


# 做什么：给用户新增一个角色，保留其他角色，重复绑定不新增
# 入参：未开启事务的会话、目标用户编号、角色标识
# 返回：新增返回 True，已绑定返回 False；用户或角色不存在时抛业务异常
def bind_user_role(db_session: Session, user_id: int, role_code: str) -> bool:
    with db_session.begin():
        # 新手注意：锁住用户行像先拿号码牌，同一用户的绑定请求依次处理。
        user_query = select(User.id).where(User.id == user_id).with_for_update()
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
    logger.info("角色绑定成功 | user_id=%s | role_id=%s", user_id, role_id)
    return True


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
