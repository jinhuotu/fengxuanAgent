"""用户管理相关的请求与响应结构。

和 schemas/auth.py 分开的原因：那边是"登录/取当前用户"的形状，这边是"管理员建人"的形状，
两件事的生命周期不同，将来改权限相关字段时不想动登录的模型。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import NewPassword
from app.schemas.rbac import RoleSummary

# 上限与 users 表的列长度对齐，超长在入库前就被拦下，不靠数据库报错
MAX_USERNAME_LENGTH = 50
MAX_DISPLAY_NAME_LENGTH = 50

DEFAULT_PAGE_NUMBER = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


# 做什么：声明"新建用户"请求体的形状
# 入参：无（类本身即结构声明）
# 返回：字段齐全且长度合法得到一个实例；否则框架转 422 / code 1000
# 说明：password 是明文，只在本次请求内存活，进库前立刻哈希，绝不落库
class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH, description="登录名")
    password: NewPassword
    display_name: str | None = Field(
        default=None, max_length=MAX_DISPLAY_NAME_LENGTH, description="显示名，可不填"
    )


# 做什么：声明管理员切换账号状态时提交的字段
# 入参：active 或 disabled
# 返回：合法的状态请求；其他状态或缺字段时校验失败
class UserStatusChangeRequest(BaseModel):
    status: Literal["active", "disabled"]


# 做什么：声明用户列表分页参数并限制单页数量
# 入参：页码与每页条数，未提供时使用默认值
# 返回：合法分页参数；页码非正数或条数超限时校验失败
class UserListQuery(BaseModel):
    page: int = Field(default=DEFAULT_PAGE_NUMBER, ge=1, description="页码，从一开始")
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="每页条数")


# 做什么：声明列表中允许对外展示的用户信息
# 入参：用户编号、用户名、显示名和状态
# 返回：不含密码及哈希的用户条目；必需字段缺失时校验失败
class UserListItem(BaseModel):
    id: int
    username: str
    display_name: str | None
    status: str
    roles: list[RoleSummary] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


# 做什么：声明统一分页结构中的列表结果
# 入参：当前页条目、用户总数、页码和每页条数
# 返回：分页结果；字段缺失或类型不合法时校验失败
class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int
    page: int
    page_size: int
