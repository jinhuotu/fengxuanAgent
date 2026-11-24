"""登录相关的请求与响应结构。

这一层只描述"请求长什么样、响应长什么样"，不放校验逻辑，也不碰数据库。
两个好处：/docs 里的接口文档直接由这些模型生成；前端照着字段名对接，不用猜。
"""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.password_policy import (
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LENGTH,
    validate_password_byte_limit,
)

# 用户名上限与 users.username 的 String(50) 保持一致，超长在入库前就被拦下
MAX_USERNAME_LENGTH = 50

PasswordInput = Annotated[str, AfterValidator(validate_password_byte_limit)]
NewPassword = Annotated[
    PasswordInput,
    Field(
        min_length=MIN_PASSWORD_LENGTH,
        description=f"至少 {MIN_PASSWORD_LENGTH} 个字符，最多 {MAX_PASSWORD_BYTES} 个 UTF-8 字节",
    ),
]

# token 类型固定 bearer：前端拼请求头时写 "Bearer {access_token}"
TOKEN_TYPE_BEARER = "bearer"


# 做什么：声明登录请求体的形状，客户端 POST /auth/login 时提交这两个字段
# 入参：无（类本身即结构声明，实例化时才带值）
# 返回：字段齐全且类型正确得到一个实例；缺字段或类型不对由框架转 422
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH, description="登录名")
    password: PasswordInput = Field(
        min_length=1, description=f"登录密码，最多 {MAX_PASSWORD_BYTES} 个 UTF-8 字节"
    )


# 做什么：声明登录成功后的响应体
# 入参：无（由 service 填值后返回给 api 层）
# 返回：三个字段，字段名与前端 src/types/api.ts 的约定一字不差，改了联调就对不上
class LoginResponse(BaseModel):
    access_token: str
    token_type: str = TOKEN_TYPE_BEARER
    expires_in: int  # 有效期，单位秒；前端可用来提示"还剩多久要重新登录"


# 做什么：声明当前用户修改密码时提交的字段
# 入参：旧密码和新密码；用户身份由登录凭证提供
# 返回：校验通过的请求对象；字段缺失或长度不符时校验失败
class ChangePasswordRequest(BaseModel):
    current_password: PasswordInput = Field(
        min_length=1, description=f"当前密码，最多 {MAX_PASSWORD_BYTES} 个 UTF-8 字节"
    )
    new_password: NewPassword


# 做什么：声明当前用户信息，GET /me 的返回体
# 入参：无（由 service 从 users 表取出后转换）
# 返回：只暴露前端需要的列，password_hash 绝不出现
class UserInfo(BaseModel):
    id: int
    username: str
    display_name: str | None
    status: str
    is_admin: bool
    created_at: datetime

    # from_attributes 允许直接拿 ORM 的 User 实例转成这个模型，
    # 不用在 service 里逐字段手抄一遍；少了它，转换会报缺少属性
    # 新手注意：像用模具扣月饼，前提是面团上真有这些花纹，这里"面团"就是 User 对象
    model_config = ConfigDict(from_attributes=True)
