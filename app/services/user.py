"""用户管理的业务逻辑。

这里只管"怎么把一个人建进库"：查重、哈希、插入。
"调用的人是不是管理员"属于鉴权，交给 api/deps.py 的依赖判断，不写在这。
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import UsernameTakenError
from app.core.security import hash_password
from app.models.user import USER_STATUS_ACTIVE, User
from app.schemas.auth import UserInfo
from app.schemas.user import UserCreate


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
