"""一次性脚本：创建第一个管理员账号。

为什么做成脚本而不是接口：我们不做公开注册，首个管理员更不能由 HTTP 请求创建，
否则谁先访问谁就是管理员。脚本只能在本机执行，天然只有你能用。

用法（在项目根目录执行）：
    uv run python scripts/create_admin.py --username admin

密码用交互式输入，不从命令行参数传：命令行参数会留在 PowerShell 的 history 文件里。
"""

import argparse
import getpass
import sys
from pathlib import Path

# 脚本是"以文件方式"直接运行的，此时项目根目录不在 sys.path 里，
# 直接 import app 会报 ModuleNotFoundError。这一行把项目根目录补进去。
# 新手注意：像你在别的楼里打电话，先得告诉总机这个分机号属于哪个楼。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402  导入顺序被迫后置，原因见上面的 sys.path

from app.core.errors import PasswordHashUnavailableError  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.core.password_policy import (  # noqa: E402
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LENGTH,
    validate_password_byte_limit,
)
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.user import USER_STATUS_ACTIVE, User  # noqa: E402

logger = get_logger(__name__)


# 做什么：解析命令行参数
# 入参：无（读 sys.argv）
# 返回：带 username 与 display_name 两个属性的对象
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建管理员账号")
    parser.add_argument("--username", required=True, help="登录名，唯一")
    parser.add_argument("--display-name", default=None, help="显示名，可不填")
    return parser.parse_args()


# 做什么：把新管理员写进 users 表；用户名已存在则不动
# 入参：username 登录名、password 明文密码、display_name 显示名
# 返回：无（副作用是插入一行；已存在时只打日志）
# 为什么先查再插：重跑脚本不该报错，也不该覆盖别人的密码
def create_admin(username: str, password: str, display_name: str | None) -> None:
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing is not None:
            logger.info("用户 %s 已存在，未做任何修改", username)
            return

        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                status=USER_STATUS_ACTIVE,
                is_admin=True,
            )
        )
        db.commit()
        logger.info("管理员 %s 创建成功", username)


# 做什么：脚本入口，把参数校验和两段输入接起来
# 入参：无
# 返回：无；参数不合法时打印原因并以退出码 1 结束
def main() -> None:
    setup_logging("dev")
    args = parse_args()

    password = getpass.getpass("请输入密码：")
    if len(password) < MIN_PASSWORD_LENGTH:
        logger.error("密码太短，至少 %d 位", MIN_PASSWORD_LENGTH)
        sys.exit(1)
    try:
        validate_password_byte_limit(password)
    except ValueError:
        logger.error("管理员 %s 的密码最多 %d 个 UTF-8 字节", args.username, MAX_PASSWORD_BYTES)
        sys.exit(1)

    confirm = getpass.getpass("请再输入一次：")
    if password != confirm:
        logger.error("两次输入的密码不一致")
        sys.exit(1)

    try:
        create_admin(args.username, password, args.display_name)
    except PasswordHashUnavailableError:
        logger.error("无法创建管理员 %s，请为当前进程配置有效的 PASSWORD_PEPPER", args.username)
        sys.exit(1)


if __name__ == "__main__":
    main()
