"""显式 pytest 插件：禁用配置文件读取，统一使用虚拟测试凭证。"""
from pydantic import SecretStr

from app.core import config as configuration_module

isolated_configuration = None
ISOLATED_DATABASE_PASSWORD = "chat-isolated-only"
ISOLATED_JWT_SECRET = "isolated-test-secret-for-regression-only"
ISOLATED_PASSWORD_PEPPER = "isolated-password-pepper-only-for-tests"


# 做什么：注册可选的独立 MySQL 测试端口
# 入参：pytest 命令行解析器
# 返回：无；不提供端口时所有常规测试仍使用自身内存库
def pytest_addoption(parser) -> None:
    parser.addoption("--chat-mysql-port", type=int, default=None, help="独立 chat_verify 容器端口")


# 做什么：在收集测试模块之前建立不读取 .env 的测试配置
# 入参：pytest 配置对象
# 返回：无；只替换测试进程的配置提供函数
def pytest_configure(config) -> None:
    global isolated_configuration
    mysql_port = config.getoption("chat_mysql_port")
    isolated_configuration = configuration_module.Settings(
        _env_file=None, db_host="127.0.0.1", db_user="root", db_port=mysql_port or 3307,
        db_name="chat_verify" if mysql_port else "isolated_test",
        db_password=ISOLATED_DATABASE_PASSWORD, jwt_secret=ISOLATED_JWT_SECRET,
        password_pepper=SecretStr(ISOLATED_PASSWORD_PEPPER),
        llm_api_key=SecretStr("fake-chat-test-key"), redis_enabled=False,
        chat_recovery_enabled=False,
    )
    configuration_module.get_settings = get_isolated_settings


# 做什么：向测试中的所有模块提供同一份虚拟配置
# 入参：无
# 返回：已初始化的配置；插件未初始化时抛明确错误
def get_isolated_settings():
    if isolated_configuration is None:
        raise RuntimeError("隔离测试配置尚未初始化")
    return isolated_configuration

