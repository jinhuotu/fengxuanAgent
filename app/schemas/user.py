"""用户管理相关的请求结构。

和 schemas/auth.py 分开的原因：那边是"登录/取当前用户"的形状，这边是"管理员建人"的形状，
两件事的生命周期不同，将来改权限相关字段时不想动登录的模型。
"""

from pydantic import BaseModel, Field

# 上限与 users 表的列长度对齐，超长在入库前就被拦下，不靠数据库报错
MAX_USERNAME_LENGTH = 50
MAX_PASSWORD_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 50

# 密码最短 8 位：和 scripts/create_admin.py 的规则保持一致，
# 否则"脚本建的人"和"接口建的人"标准不一样，以后查起来很别扭
MIN_PASSWORD_LENGTH = 8


# 做什么：声明"新建用户"请求体的形状
# 入参：无（类本身即结构声明）
# 返回：字段齐全且长度合法得到一个实例；否则框架转 422 / code 1000
# 说明：password 是明文，只在本次请求内存活，进库前立刻哈希，绝不落库
class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH, description="登录名")
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="初始密码，至少 8 位",
    )
    display_name: str | None = Field(
        default=None, max_length=MAX_DISPLAY_NAME_LENGTH, description="显示名，可不填"
    )
