"""配置集中管理。

全项目只有这一个地方读取环境变量。任何模块要用配置，都从这里拿 Settings，
禁止直接写 os.environ[...]，否则"项目到底有哪些配置"会散落在十几个文件里。
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 默认值集中在这里。改默认值只改这一处，每一行都写清取值理由。
DEFAULT_DB_HOST = "127.0.0.1"          # 本地开发库；服务器上的值由 .env 覆盖
DEFAULT_DB_PORT = 3306                 # MySQL 默认端口
DEFAULT_DB_USER = "root"               # 仅开发用；生产应建专用账号
DEFAULT_DB_NAME = "fengxuan_agent"     # 库名，建库时保持一致
DEFAULT_APP_ENV = "dev"
DEFAULT_JWT_EXPIRE_MINUTES = 30        # 与 docs/progress.md 拍板的 30 分钟一致
DEFAULT_CORS_ORIGINS = "http://127.0.0.1:5176,http://localhost:5176"


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

    app_env: Literal["dev", "prod"] = DEFAULT_APP_ENV
    jwt_expire_minutes: int = DEFAULT_JWT_EXPIRE_MINUTES

    # 存原始字符串，不存列表：pydantic-settings 遇到 list 类型会尝试按 JSON 解析，
    # .env 里写 a,b 这种逗号分隔会直接报错。拆分交给下面的属性。
    cors_origins: str = DEFAULT_CORS_ORIGINS

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
