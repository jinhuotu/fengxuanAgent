"""统一业务异常与全局错误响应。

为什么单独一个文件：如果每个接口自己 try/except 拼返回值，
错误的结构会五花八门，前端没法统一处理，日志里也看不出"这是业务错误还是程序 bug"。
这里把两类错误分开：业务错误（用户操作不对，可预期）走 BusinessError 和它的子类；
程序错误（代码 bug、数据库断连，不可预期）走全局兜底，只记日志，不向用户暴露细节。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 错误码规则：1000-1999 参数与认证类；2000-2999 权限类；5000 起是系统级。
# 前端靠 code 判断具体原因，靠 HTTP 状态码判断大类，两者都要对，不能一律返回 200。
CODE_INVALID_PARAMS = 1000
CODE_INVALID_CREDENTIALS = 1001
CODE_TOKEN_INVALID = 1002
CODE_ACCOUNT_DISABLED = 1003
CODE_ACCOUNT_LOCKED = 1004
CODE_USERNAME_TAKEN = 1005
CODE_PERMISSION_DENIED = 2001
CODE_INTERNAL_ERROR = 5000

DEFAULT_HTTP_STATUS = 400
INTERNAL_ERROR_MESSAGE = "服务器内部错误，请稍后重试"


class BusinessError(Exception):
    """业务异常基类。

    子类只需要改三个类属性：code、http_status、message。
    抛的时候可以覆盖 message，但不要改 code —— code 是给前端做分支判断的契约。
    """

    code = CODE_INVALID_PARAMS
    http_status = DEFAULT_HTTP_STATUS
    message = "请求处理失败"

    def __init__(self, message: str | None = None) -> None:
        # 没传就用类上定义的默认文案，保证同一种错误在任何地方提示一致
        self.message = message or self.message
        super().__init__(self.message)


# 做什么：账号或密码不对
# 入参：可选自定义提示
# 返回：无（抛出后由全局处理器转成 401）
# 注意：提示统一为"用户名或密码错误"，不区分是账号不存在还是密码错，防止被用来探测账号
class InvalidCredentialsError(BusinessError):
    code = CODE_INVALID_CREDENTIALS
    http_status = 401
    message = "用户名或密码错误"


# 做什么：token 缺失、过期、签名不对
# 入参：可选自定义提示
# 返回：无（抛出后转 401）
class TokenInvalidError(BusinessError):
    code = CODE_TOKEN_INVALID
    http_status = 401
    message = "登录状态无效或已过期，请重新登录"


# 做什么：账号被管理员停用
# 入参：可选自定义提示
# 返回：无（抛出后转 403，表示"身份已知但无权限"）
class AccountDisabledError(BusinessError):
    code = CODE_ACCOUNT_DISABLED
    http_status = 403
    message = "账号已被停用，请联系管理员"


# 做什么：连续登录失败次数过多，账号被临时锁定
# 入参：可选自定义提示
# 返回：无（抛出后转 429，表示"短期内别再来试"，与 401 的"凭证不对"区分开）
# 为什么用 429：前端能一眼分清"密码错"和"被锁"，不用弹两次同样的提示
class AccountLockedError(BusinessError):
    code = CODE_ACCOUNT_LOCKED
    http_status = 429
    message = "登录失败次数过多，请 10 分钟后再试"


# 做什么：新建用户时用户名已被占用
# 入参：可选自定义提示
# 返回：无（抛出后转 409）
# 为什么用 409：表示"请求本身没问题，但跟服务器现状冲突"，比 400 更能说清原因
class UsernameTakenError(BusinessError):
    code = CODE_USERNAME_TAKEN
    http_status = 409
    message = "用户名已存在，换一个"


# 做什么：身份合法，但没有执行这个操作的权限
# 入参：可选自定义提示
# 返回：无（抛出后转 403）
# 为什么和 AccountDisabledError 分开：那个是"账号被停用"，这个是"账号正常但不该干这事"，
# 前端处理方式不同，日志排查时也能一眼分清是账号问题还是越权
# 错误码进 2000 段：按文件顶部的约定，2000-2999 留给权限类
class PermissionDeniedError(BusinessError):
    code = CODE_PERMISSION_DENIED
    http_status = 403
    message = "没有权限执行此操作"


# 做什么：把上面这些异常接住，转成统一响应结构
# 入参：app 是 FastAPI 实例（在 main.py 里装配时传入）
# 返回：无（副作用是给 app 注册三个异常处理器）
# 为什么三个：业务错误照原样返回给用户；参数校验错误转成可读提示；其余一律 500 且只记日志。
def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 只取第一条错误，并拼成"字段名 + 原因"，比原生的英文报错对前端友好
        first_error = exc.errors()[0]
        field_path = ".".join(str(part) for part in first_error["loc"] if part != "body")
        readable_message = f"参数不合法：{field_path} {first_error['msg']}"
        return JSONResponse(
            status_code=422,
            content={"code": CODE_INVALID_PARAMS, "message": readable_message, "data": None},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 系统错误不把堆栈和细节返回给前端：那是给攻击者看的。
        # 完整堆栈只进日志，日志里带请求路径，方便定位。
        logging.getLogger(__name__).exception(
            "未处理异常 | %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"code": CODE_INTERNAL_ERROR, "message": INTERNAL_ERROR_MESSAGE, "data": None},
        )
