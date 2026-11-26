"""用户管理的业务逻辑。

这里负责创建用户、分页读取公开信息、切换账号状态和逻辑删除账号。
"调用的人是不是管理员"属于鉴权，交给 api/deps.py 的依赖判断，不写在这。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer

from app.core.errors import (
    CannotModifySelfError,
    LastAdminProtectedError,
    UserNotFoundError,
    UsernameTakenError,
)
from app.core.security import hash_password
from app.models.user import IS_DELETED, IS_NOT_DELETED, USER_STATUS_ACTIVE, USER_STATUS_DISABLED, User
from app.models.rbac import Role, UserRole
from app.schemas.rbac import RoleSummary
from app.schemas.auth import UserInfo
from app.schemas.user import UserCreate, UserListItem, UserListQuery, UserListResponse
from app.services.rbac import ADMIN_ROLE_CODE, count_active_role_holders

logger = logging.getLogger(__name__)


# 做什么：新建一个普通账号（是否需要管理员身份由上层依赖把关）
# 入参：db_session 请求级会话；payload 已通过 Pydantic 校验的表单
# 返回：UserInfo，status 固定 active、is_admin 固定 False
# 失败：用户名已存在 -> UsernameTakenError（409 / 1005）
def create_user(db_session: Session, payload: UserCreate) -> UserInfo:
    # 先查一次是为了给出人话提示；真正防止重复的是数据库的唯一索引。
    # 两个人几乎同时提交同名时，这里的查询都会放行，最后靠下面 commit 兜底
    existing_user = db_session.scalars(
        select(User).where(User.username == payload.username)
    ).one_or_none()
    if existing_user is not None:
        raise UsernameTakenError()

    new_user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        status=USER_STATUS_ACTIVE,
        is_admin=False,
    )
    db_session.add(new_user)
    try:
        db_session.commit()
    except IntegrityError:
        # 并发撞在唯一索引上：回滚掉这条半成品，再转成可读的业务错误
        db_session.rollback()
        raise UsernameTakenError() from None

    # commit 后对象里的属性会过期（数据库生成的 id、created_at 还没回填），
    # refresh 重新从库里读一次，UserInfo 才能拿到完整数据
    db_session.refresh(new_user)
    return UserInfo.model_validate(new_user)


# 做什么：确认停用目标后仍有至少一名启用的管理员
# 入参：处于写事务的会话和已锁定的目标用户
# 返回：无；目标是最后一名启用管理员时抛 LastAdminProtectedError
def _ensure_admin_remains_after_disable(db_session: Session, target_user: User) -> None:
    if target_user.status != USER_STATUS_ACTIVE:
        return
    # 使用与撤销 admin 同一把角色行锁，避免两种操作各自检查通过。
    role_query = select(Role.id).where(Role.code == ADMIN_ROLE_CODE).with_for_update()
    admin_role_id = db_session.scalar(role_query)
    if admin_role_id is None:
        return
    binding_query = select(UserRole.id).where(
        UserRole.user_id == target_user.id, UserRole.role_id == admin_role_id
    )
    if db_session.scalar(binding_query) is None:
        return
    if count_active_role_holders(db_session, ADMIN_ROLE_CODE, target_user.id) == 0:
        raise LastAdminProtectedError()


# 做什么：切换他人的账号状态，并在停用时原子递增令牌版本
# 入参：未开启事务的会话、操作者编号、目标用户编号和已校验的状态
# 返回：改变成功 True，状态相同 False；操作本人、目标不存在或需保护管理员时抛业务异常
def change_user_status(db_session: Session, operator_id: int, user_id: int, status: str) -> bool:
    if operator_id == user_id:
        raise CannotModifySelfError()
    with db_session.begin():
        # 已删除的账号当作不存在：不能再被启用，否则它会带着"正常"状态回到列表里
        user_query = select(User).options(undefer(User.token_version)).where(
            User.id == user_id, User.is_deleted == IS_NOT_DELETED
        )
        target_user = db_session.scalar(user_query.with_for_update())
        if target_user is None:
            raise UserNotFoundError()
        if target_user.status == status:
            return False
        if status == USER_STATUS_DISABLED:
            _ensure_admin_remains_after_disable(db_session, target_user)
            target_user.token_version += 1
        target_user.status = status
    logger.info("账号状态更新 | operator_id=%s | user_id=%s | status=%s", operator_id, user_id, status)
    return True


# 做什么：逻辑删除他人账号：只打删除标记，不删这一行，并让已签发的登录凭证全部失效
# 入参：未开启事务的会话、操作者编号和目标用户编号
# 返回：无；操作本人、目标不存在（含此前已删除）或需保护最后一名启用的管理员时抛业务异常
def delete_user(db_session: Session, operator_id: int, user_id: int) -> None:
    if operator_id == user_id:
        raise CannotModifySelfError("不能删除自己的账号")
    with db_session.begin():
        # 与停用用同一把用户行锁：同一账号的删除和状态切换排队处理，不会互相覆盖。
        # 查不到也包括"已经删过"，对调用方统一表现为用户不存在。
        user_query = select(User).options(undefer(User.token_version)).where(
            User.id == user_id, User.is_deleted == IS_NOT_DELETED
        )
        target_user = db_session.scalar(user_query.with_for_update())
        if target_user is None:
            raise UserNotFoundError()
        # 与停用共用同一条保护：目标若是一名启用的管理员，删掉之后系统还得剩至少一名。
        # 停用状态的管理员本来就不计入，所以删一个已停用的管理员不会被拦。
        _ensure_admin_remains_after_disable(db_session, target_user)
        # 递增令牌版本：这个账号手上的登录凭证立刻失效；将来即使恢复账号，旧凭证也不能再用
        target_user.token_version += 1
        target_user.is_deleted = IS_DELETED
    logger.info("用户逻辑删除 | operator_id=%s | user_id=%s", operator_id, user_id)


# 做什么：按用户编号升序分页查询未删除的用户，仅返回公开字段
# 入参：数据库会话和已校验的分页参数
# 返回：当前页、未删除的总人数及分页信息；数据库错误直接抛出
def list_users(db_session: Session, query: UserListQuery) -> UserListResponse:
    total_users = db_session.scalar(select(func.count()).select_from(User).where(User.is_deleted == IS_NOT_DELETED))
    offset_count = (query.page - 1) * query.page_size
    user_query = select(User.id, User.username, User.display_name, User.status).where(User.is_deleted == IS_NOT_DELETED)
    user_query = user_query.order_by(User.id).offset(offset_count).limit(query.page_size)
    user_rows = db_session.execute(user_query).mappings().all()
    user_ids = [user_row["id"] for user_row in user_rows]
    roles_by_user = get_roles_by_user(db_session, user_ids)
    user_items: list[UserListItem] = []
    for user_row in user_rows:
        user_item = UserListItem.model_validate(user_row)
        user_item.roles = roles_by_user.get(user_item.id, [])
        user_items.append(user_item)
    return UserListResponse(
        items=user_items, total=total_users, page=query.page, page_size=query.page_size
    )


# 做什么：一次查询当前页用户的角色，并按用户编号分组
# 入参：数据库会话和当前页用户编号列表
# 返回：用户编号到角色列表的映射；空页返回空字典，数据库错误直接抛出
def get_roles_by_user(db_session: Session, user_ids: list[int]) -> dict[int, list[RoleSummary]]:
    if not user_ids:
        return {}
    role_query = select(UserRole.user_id, Role.code, Role.name).join(Role, Role.id == UserRole.role_id)
    role_query = role_query.where(UserRole.user_id.in_(user_ids)).order_by(UserRole.user_id, Role.id)
    role_rows = db_session.execute(role_query).all()
    # 新手注意：按用户分篮子，每个篮子只装这个用户的角色。
    roles_by_user: dict[int, list[RoleSummary]] = {}
    for role_row in role_rows:
        role_item = RoleSummary(code=role_row.code, name=role_row.name)
        roles_by_user.setdefault(role_row.user_id, []).append(role_item)
    return roles_by_user
