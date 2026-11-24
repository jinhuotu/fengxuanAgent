"""用户管理的业务逻辑。

这里负责创建用户和分页读取公开的用户信息。
"调用的人是不是管理员"属于鉴权，交给 api/deps.py 的依赖判断，不写在这。
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import UsernameTakenError
from app.core.security import hash_password
from app.models.user import USER_STATUS_ACTIVE, User
from app.models.rbac import Role, UserRole
from app.schemas.rbac import RoleSummary
from app.schemas.auth import UserInfo
from app.schemas.user import UserCreate, UserListItem, UserListQuery, UserListResponse


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


# 做什么：按用户编号升序分页查询，仅返回公开字段
# 入参：数据库会话和已校验的分页参数
# 返回：当前页、总人数及分页信息；数据库错误直接抛出
def list_users(db_session: Session, query: UserListQuery) -> UserListResponse:
    total_users = db_session.scalar(select(func.count()).select_from(User))
    offset_count = (query.page - 1) * query.page_size
    user_query = select(User.id, User.username, User.display_name, User.status)
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
