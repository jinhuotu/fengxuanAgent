"""从环境变量加载的应用配置。"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Fengxuan Agent Backend"
    app_env: str = "dev"
    app_debug: bool = True
    app_port: int = 8000

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "root"
    mysql_db: str = "agent_backend"
    # 兼容使用 MYSQL_DATABASE 的环境变量名。
    mysql_database: str | None = None

    redis_url: str = "redis://127.0.0.1:6379/0"
    chroma_persist_directory: str = "./data/chroma"

    jwt_secret_key: str = "please_change_me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    default_temperature: float = 0.7
    default_context_window: int = 8192
    secret_encrypt_key: str = "please_change_encrypt_key_32chars"

    # china-testing/bazi 本地仓库路径（空则禁用八字相关内置工具）
    bazi_home: str = "D:/code/tools/bazi"
    # 可选：显式指定解释器；默认使用 {bazi_home}/.venv 下的 python
    bazi_python: str | None = None
    bazi_subprocess_timeout_seconds: int = 90
    bazi_max_output_chars: int = 16000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def sqlalchemy_database_uri(self) -> str:
        db_name = self.mysql_db or self.mysql_database or "agent_backend"
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{db_name}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
