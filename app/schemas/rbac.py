"""角色绑定接口的请求结构。"""
from pydantic import BaseModel, Field

MAX_ROLE_CODE_LENGTH = 50


# 做什么：声明给用户追加角色时提交的字段
# 入参：角色标识，例如 admin 或 user
# 返回：合法请求对象；缺失、为空或过长时校验失败
class RoleBindRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=MAX_ROLE_CODE_LENGTH, description="角色标识")


# 做什么：声明用户已绑定角色的公开信息
# 入参：角色标识与显示名称
# 返回：角色摘要；字段缺失时校验失败
class RoleSummary(BaseModel):
    code: str
    name: str
