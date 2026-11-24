"""配置集中管理。

全项目只有这一个地方读取环境变量。任何模块要用配置，都从这里拿 Settings，
禁止直接写 os.environ[...]，否则"项目到底有哪些配置"会散落在十几个文件里。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 默认值集中在这里。改默认值只改这一处，每一行都写清取值理由。
DEFAULT_DB_HOST = "127.0.0.1"          # 本地开发库；服务器上的值由 .env 覆盖
DEFAULT_DB_PORT = 3306                 # MySQL 默认端口
DEFAULT_DB_USER = "root"               # 仅开发用；生产应建专用账号
DEFAULT_DB_NAME = "fengxuan_agent"     # 库名，建库时保持一致
DEFAULT_APP_ENV = "dev"
DEFAULT_JWT_EXPIRE_MINUTES = 30        # 与 docs/progress.md 拍板的 30 分钟一致
DEFAULT_CORS_ORIGINS = "http://127.0.0.1:5176,http://localhost:5176,http://127.0.0.1:5178,http://localhost:5178"
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-flash"
DEFAULT_LLM_TIMEOUT_SECONDS = 60  # 限制单次请求等待时间，避免一直占用请求线程
DEFAULT_LLM_MAX_RETRIES = 0  # 初次接入默认不重试，避免失败后重复发起付费请求
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 2048  # 首版回答长度上限，不是模型的上下文容量


class Settings(BaseSettings):
    """应用配置对象。

    字段名与环境变量同名即可（大小写不敏感），例如 db_host 对应 .env 里的 DB_HOST。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env 里多出来的键直接忽略：换台机器、多写一行注释性配置时不会启动失败
        extra="ignore",
    )

    db_host: str = DEFAULT_DB_HOST
    db_port: int = DEFAULT_DB_PORT
    db_user: str = DEFAULT_DB_USER
    db_name: str = DEFAULT_DB_NAME

    # 没有默认值：缺了就必须在启动时失败。
    # 这两个值如果给了默认，就会悄悄用空密码连库、用固定字符串签发 token。
    db_password: str
    jwt_secret: str
    # 写入和校验密码哈希都需要部署者提供的同一份独立密钥。
    password_pepper: SecretStr = SecretStr("")

    app_env: Literal["dev", "prod"] = DEFAULT_APP_ENV
    jwt_expire_minutes: int = DEFAULT_JWT_EXPIRE_MINUTES

    # 存原始字符串，不存列表：pydantic-settings 遇到 list 类型会尝试按 JSON 解析，
    # .env 里写 a,b 这种逗号分隔会直接报错。拆分交给下面的属性。
    cors_origins: str = DEFAULT_CORS_ORIGINS

    # 基础地址不带 /chat/completions，后续由模型客户端统一追加路径。
    llm_base_url: str = Field(default=DEFAULT_LLM_BASE_URL, min_length=1)
    llm_model: str = Field(default=DEFAULT_LLM_MODEL, min_length=1)
    # 新手注意：SecretStr 像遮住证件号码，展示配置时隐藏密钥，但不是加密存储。
    llm_api_key: SecretStr = SecretStr("")  # 先允许未配置，调用前由模型客户端检查
    llm_timeout_seconds: float = Field(default=DEFAULT_LLM_TIMEOUT_SECONDS, gt=0)
    llm_max_retries: int = Field(default=DEFAULT_LLM_MAX_RETRIES, ge=0, le=3)
    llm_max_output_tokens: int = Field(default=DEFAULT_LLM_MAX_OUTPUT_TOKENS, gt=0)

    # Redis 保存对话缓存和登录锁，停用或故障时分别回源 MySQL 和进程内存。
    redis_enabled: bool = True
    redis_host: str = DEFAULT_DB_HOST
    redis_port: int = Field(default=6380, ge=1, le=65535)
    redis_database: int = Field(default=0, ge=0)
    redis_password: SecretStr = SecretStr("")
    redis_timeout_seconds: float = Field(default=0.3, gt=0)
    chat_cache_ttl_seconds: int = Field(default=1800, gt=0)
    chat_context_rounds: int = Field(default=10, ge=1, le=50)
    chat_context_max_characters: int = Field(default=12000, ge=4000, le=100000)
    chat_max_answer_characters: int = Field(default=50000, ge=1, le=1000000)
    # 仅在有新增正文时按间隔保存，避免逐片段写库；正常结束仍会保存最终结果。
    chat_partial_save_interval_seconds: float = Field(default=2, ge=0.1, le=30)
    # 后台只处理到期占用；每轮限制行数，避免长事务拖住正常请求。
    chat_recovery_enabled: bool = True
    chat_recovery_interval_seconds: float = Field(default=30, ge=0.1, le=3600)
    chat_recovery_batch_size: int = Field(default=100, ge=1, le=500)
    chat_global_concurrency_limit: int = Field(default=10, ge=1)
    # 应用采用的兼容预算窗口，可低于供应商上限；不是自动探测的模型容量。
    llm_context_window_tokens: int = Field(default=32768, gt=0)
    chat_input_token_budget: int = Field(default=24000, gt=0)
    chat_context_safety_tokens: int = Field(default=512, ge=0)
    chat_prewarm_enabled: bool = True
    chat_prewarm_queue_capacity: int = Field(default=20, ge=1, le=1000)
    chat_prewarm_workers: int = Field(default=2, ge=1, le=8)
    chat_prewarm_shutdown_seconds: float = Field(default=1, ge=0.01, le=30)


    # 做什么：把逗号分隔的跨域来源拆成列表给 CORS 中间件用
    # 入参：无（读 self.cors_origins）
    # 返回：去空白后的来源列表；空字符串返回空列表
    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    # 做什么：拼出 SQLAlchemy 使用的数据库连接串
    # 入参：无（读本对象的连接相关字段）
    # 返回：带 charset=utf8mb4 的连接串
    @property
    def database_url(self) -> str:
        # 注意：密码里如果出现 @ : / ? 这些字符，需要先做 URL 编码，否则连接串会拼坏。
        # 阶段一先用纯字母数字密码，遇到特殊字符再加 quote_plus。
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            "?charset=utf8mb4"
        )


# 做什么：返回全局唯一的配置对象
# 入参：无
# 返回：Settings 实例；第一次调用时读取 .env 并校验，之后直接返回缓存
# 为什么缓存：FastAPI 每个请求都可能需要配置，每次重新解析环境变量纯属浪费；
#             同时保证"启动时校验一次"这件事只发生一次，配置有问题会立刻暴露。
@lru_cache
def get_settings() -> Settings:
    return Settings()
