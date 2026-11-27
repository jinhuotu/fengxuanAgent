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

# 错误码规则：1000-1999 认证类；2000-2999 权限类；3000-3999 对话类。
# 4000-4999 知识库类；6000-6999 计划类；5000 起是系统级。
# 前端靠 code 判断具体原因，靠 HTTP 状态码判断大类，两者都要对，不能一律返回 200。
CODE_INVALID_PARAMS = 1000
CODE_INVALID_CREDENTIALS = 1001
CODE_TOKEN_INVALID = 1002
CODE_ACCOUNT_DISABLED = 1003
CODE_ACCOUNT_LOCKED = 1004
CODE_USERNAME_TAKEN = 1005
CODE_CURRENT_PASSWORD_INCORRECT = 1006
CODE_USER_NOT_FOUND = 1007
CODE_PERMISSION_DENIED = 2001
CODE_ROLE_NOT_FOUND = 2002
CODE_CANNOT_MODIFY_SELF = 2003
CODE_LAST_ADMIN_PROTECTED = 2004
CODE_KNOWLEDGE_BASE_NOT_FOUND = 4001
CODE_KNOWLEDGE_DOCUMENT_NOT_FOUND = 4002
CODE_KNOWLEDGE_CHUNK_NOT_FOUND = 4003
CODE_KNOWLEDGE_UPLOAD_INVALID = 4004
CODE_KNOWLEDGE_UPLOAD_TYPE = 4005
CODE_KNOWLEDGE_UPLOAD_SIZE = 4006
CODE_KNOWLEDGE_UPLOAD_PARSE = 4007
CODE_KNOWLEDGE_UPLOAD_CONTENT = 4008
CODE_KNOWLEDGE_UPLOAD_TIMEOUT = 4009
CODE_PLAN_GOAL_NOT_FOUND = 6001
CODE_PLAN_KEY_RESULT_NOT_FOUND = 6002
CODE_PLAN_PROJECT_NOT_FOUND = 6003
CODE_PLAN_TASK_NOT_FOUND = 6004
CODE_PLAN_STUDY_ITEM_NOT_FOUND = 6005
CODE_PLAN_DATE_RANGE = 6006
CODE_INTERNAL_ERROR = 5000

DEFAULT_HTTP_STATUS = 400
INTERNAL_ERROR_MESSAGE = "服务器内部错误，请稍后重试"

# 这几种错误不写日志：1000 参数校验每次都带用户输入细节，1001~1004 由登录保护与鉴权各自记录，
# 而且过期令牌属于高频噪声，重复记只会淹没真正的异常。
CODE_NOT_LOGGED = frozenset({CODE_INVALID_PARAMS, CODE_INVALID_CREDENTIALS, CODE_TOKEN_INVALID, CODE_ACCOUNT_DISABLED, CODE_ACCOUNT_LOCKED})


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


# 做什么：修改密码时旧密码校验失败
# 入参：可选自定义提示
# 返回：抛出后由全局处理器转成 400，密码保持不变
class CurrentPasswordIncorrectError(BusinessError):
    code = CODE_CURRENT_PASSWORD_INCORRECT
    message = "旧密码错误"


# 做什么：新版密码哈希所需的密钥不可用
# 入参：无，统一使用公开错误文案
# 返回：抛出后由全局处理器转成 503
class PasswordHashUnavailableError(BusinessError):
    code = CODE_INTERNAL_ERROR
    http_status = 503
    message = "密码服务暂不可用，请联系管理员"


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


# 做什么：角色绑定时找不到目标用户
# 入参：可选自定义提示
# 返回：抛出后由全局处理器转成 404，数据库不写入
class UserNotFoundError(BusinessError):
    code = CODE_USER_NOT_FOUND
    http_status = 404
    message = "用户不存在"


# 做什么：角色绑定时找不到指定角色
# 入参：可选自定义提示
# 返回：抛出后由全局处理器转成 404，数据库不写入
class RoleNotFoundError(BusinessError):
    code = CODE_ROLE_NOT_FOUND
    http_status = 404
    message = "角色不存在"


# 做什么：阻止操作者修改自己的角色或状态
# 入参：无，使用固定错误文案
# 返回：抛出后由全局处理器转成 403/2003
class CannotModifySelfError(BusinessError):
    code = CODE_CANNOT_MODIFY_SELF
    http_status = 403
    message = "不能修改自己的角色或状态"


# 做什么：阻止撤销最后一名启用管理员的角色
# 入参：无，使用固定错误文案
# 返回：抛出后由全局处理器转成 409/2004
class LastAdminProtectedError(BusinessError):
    code = CODE_LAST_ADMIN_PROTECTED
    http_status = 409
    message = "系统至少保留一名管理员"


# 做什么：统一处理不存在、已删除或不属于当前用户的知识库
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/4001
class KnowledgeBaseNotFoundError(BusinessError):
    code = CODE_KNOWLEDGE_BASE_NOT_FOUND
    http_status = 404
    message = "知识库不存在"


# 做什么：统一处理不存在、已删除或不属于指定库和用户的文档
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/4002
class KnowledgeDocumentNotFoundError(BusinessError):
    code = CODE_KNOWLEDGE_DOCUMENT_NOT_FOUND
    http_status = 404
    message = "知识库文档不存在"


# 做什么：统一处理不可见的切片或切片所属文档
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/4003
class KnowledgeChunkNotFoundError(BusinessError):
    code = CODE_KNOWLEDGE_CHUNK_NOT_FOUND
    http_status = 404
    message = "知识库切片不存在"


class KnowledgeUploadInvalidError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_INVALID
    message = "请通过 file 字段上传一个文件"


class KnowledgeUploadTypeError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_TYPE
    http_status = 415
    message = "仅支持 .txt、.md、.docx、.pdf 文件"


class KnowledgeUploadSizeError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_SIZE
    http_status = 413
    message = "文件不能超过 10 MB，请拆分后上传"


class KnowledgeUploadParseError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_PARSE
    http_status = 422
    message = "文件解析失败，请确认文件完整且未加密"


class KnowledgeUploadContentError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_CONTENT
    http_status = 422
    message = "文件没有可用正文，请检查内容后上传"


class KnowledgeUploadTimeoutError(BusinessError):
    code = CODE_KNOWLEDGE_UPLOAD_TIMEOUT
    http_status = 408
    message = "文件解析超时，请拆分后重试"


# 做什么：统一处理不存在、已删除或不属于当前用户的目标
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/6001
class PlanGoalNotFoundError(BusinessError):
    code = CODE_PLAN_GOAL_NOT_FOUND
    http_status = 404
    message = "目标不存在"


# 做什么：统一处理不存在或不属于当前用户的关键结果
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/6002
class PlanKeyResultNotFoundError(BusinessError):
    code = CODE_PLAN_KEY_RESULT_NOT_FOUND
    http_status = 404
    message = "关键结果不存在"


# 做什么：统一处理不存在、已删除或不属于当前用户的计划
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/6003
class PlanProjectNotFoundError(BusinessError):
    code = CODE_PLAN_PROJECT_NOT_FOUND
    http_status = 404
    message = "计划不存在"


# 做什么：统一处理不存在或不属于当前用户的任务
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/6004
class PlanTaskNotFoundError(BusinessError):
    code = CODE_PLAN_TASK_NOT_FOUND
    http_status = 404
    message = "任务不存在"


# 做什么：统一处理不存在或不属于当前用户的学习单元
# 入参：无，使用公开错误文案
# 返回：抛出后由全局处理器转成 404/6005
class PlanStudyItemNotFoundError(BusinessError):
    code = CODE_PLAN_STUDY_ITEM_NOT_FOUND
    http_status = 404
    message = "学习单元不存在"


# 做什么：计划或目标的结束日期早于开始日期
# 入参：可选自定义提示
# 返回：抛出后由全局处理器转成 400/6006
class PlanDateRangeError(BusinessError):
    code = CODE_PLAN_DATE_RANGE
    message = "结束日期不能早于开始日期"


# 做什么：把上面这些异常接住，转成统一响应结构
# 入参：app 是 FastAPI 实例（在 main.py 里装配时传入）
# 返回：无（副作用是给 app 注册三个异常处理器）
# 为什么三个：业务错误照原样返回给用户；参数校验错误转成可读提示；其余一律 500 且只记日志。
def register_exception_handlers(app: FastAPI) -> None:
    logger = logging.getLogger(__name__)

    @app.exception_handler(BusinessError)
    async def handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
        # 预期内的业务错误也留一条记录，否则用户说"点了没反应"时日志里什么都查不到。
        # 认证类的 1000~1004 由登录保护各自记录、或属于高频噪声，这里不重复记。
        if exc.code not in CODE_NOT_LOGGED:
            logger.info(
                "业务错误 | %s %s | code=%s | http=%s | user_id=%s",
                request.method, request.url.path, exc.code, exc.http_status,
                getattr(request.state, "user_id", "-"),
            )
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
