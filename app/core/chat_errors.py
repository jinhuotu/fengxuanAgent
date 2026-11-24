"""对话错误：安全文案可以直接展示，上游响应和密钥不进入错误信息。"""
from app.core.errors import BusinessError


class ChatNotFoundError(BusinessError):
    code = 3001
    http_status = 404
    message = "会话不存在或无权访问"


class ChatBusyError(BusinessError):
    code = 3002
    http_status = 409
    message = "当前会话正在生成回答，请稍后再试"


class ChatDuplicateError(BusinessError):
    code = 3003
    http_status = 409
    message = "该问题已提交，请刷新历史查看结果"


class ChatCapacityError(BusinessError):
    code = 3004
    http_status = 429
    message = "当前全站生成任务已满，请稍后重新发送"


class ChatCapacityUnavailableError(BusinessError):
    code = 3005
    http_status = 503
    message = "对话并发控制尚未就绪，请联系管理员检查数据库迁移"


class ChatRequestNotFoundError(BusinessError):
    code = 3006
    http_status = 404
    message = "未找到该请求的已提交记录，请稍后再次查询"


class ChatRetryNotAllowedError(BusinessError):
    code = 3007
    http_status = 409
    message = "只能重试当前会话最近失败或中断的问题，请刷新结果后再试"


class ModelUnavailableError(BusinessError):
    code = 5001
    http_status = 503
    message = "模型暂不可用，请稍后重试"
    error_code = "model_unavailable"


class ModelNotConfiguredError(BusinessError):
    code = 5002
    http_status = 503
    message = "尚未配置模型 API Key，请联系管理员"


class ModelAuthenticationError(ModelUnavailableError):
    code = 5003
    error_code = "model_authentication_failed"
    message = "模型服务认证失败，请联系管理员检查 API Key"


class ModelPermissionError(ModelUnavailableError):
    code = 5004
    error_code = "model_permission_denied"
    message = "模型服务拒绝访问，请联系管理员检查模型权限"


class ModelRateLimitError(ModelUnavailableError):
    code = 5005
    error_code = "model_rate_limited"
    message = "模型服务请求过于频繁，请稍后重新发送"


class ModelPaymentError(ModelUnavailableError):
    code = 5006
    error_code = "model_payment_required"
    message = "模型服务账户需要充值或检查计费状态，请联系管理员"


class ModelRequestError(ModelUnavailableError):
    code = 5007
    error_code = "model_request_rejected"
    message = "模型服务未接受请求，请联系管理员检查模型名称和请求配置"


class ModelTimeoutError(ModelUnavailableError):
    code = 5008
    error_code = "model_timeout"
    message = "模型响应超时，请重新发送"


class ModelConnectionError(ModelUnavailableError):
    code = 5009
    error_code = "model_connection_failed"
    message = "模型服务连接异常，请稍后重新发送"


class ModelProtocolError(ModelUnavailableError):
    code = 5010
    error_code = "model_protocol_error"
    message = "模型返回格式异常或连接提前结束，请重新发送"


class ModelContextBudgetError(ModelUnavailableError):
    code = 5011
    http_status = 400
    error_code = "model_context_budget_exceeded"
    message = "当前问题与系统提示词超过本次输入预算，请缩短问题或联系管理员调整配置"


class ModelBudgetConfigurationError(ModelUnavailableError):
    code = 5012
    http_status = 503
    error_code = "model_budget_configuration_invalid"
    message = "模型预算配置不足以预留输出和安全空间，请联系管理员调整配置"


# 新手注意：错误标识像故障标签，历史记录和实时事件使用同一张对照表。
MODEL_ERROR_TYPES = (
    ModelUnavailableError, ModelAuthenticationError, ModelPermissionError, ModelRateLimitError,
    ModelPaymentError, ModelRequestError, ModelTimeoutError, ModelConnectionError, ModelProtocolError,
    ModelContextBudgetError, ModelBudgetConfigurationError,
)
MODEL_EVENT_CODES = {error_type.error_code: error_type.code for error_type in MODEL_ERROR_TYPES}

