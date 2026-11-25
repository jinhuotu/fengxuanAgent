"""日志配置。

全项目只用 logging，禁止 print。原因很实际：print 没有级别、没有时间、没有来源，
出问题时你在几百行输出里找不到到底是哪个模块打的。
日志统一走 stdout（控制台），以后放进 Docker 由容器收集，不往文件里写。
"""

import logging
import sys
from contextvars import ContextVar

# 当前请求编号：中间件写入，日志格式统一读取，一次请求的多条日志靠它串起来。
# 默认 "-" 表示不是请求内产生的日志（例如启动、关闭、后台任务）。
REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")

# 日志格式：时间 | 级别 | 模块名 | 请求编号 | 内容。
# 模块名必须留，否则看到"出错了"三个字根本不知道是谁出的错。
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(request_id)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 环境到级别的映射：开发看全部细节，生产只看 INFO 以上，避免日志被调试信息淹没
LOG_LEVEL_BY_ENV = {"dev": logging.DEBUG, "prod": logging.INFO}


class RequestIdFormatter(logging.Formatter):
    """把当前请求编号补进每条日志，业务代码不必各自传参。"""

    # 做什么：先用上下文里的请求编号补齐记录，再交给父类格式化
    # 入参：logging 产生的日志记录
    # 返回：格式化后的整行文本
    def format(self, record: logging.LogRecord) -> str:
        record.request_id = REQUEST_ID.get()
        return super().format(record)


# 做什么：设置当前请求编号
# 入参：请求编号
# 返回：可用于恢复的令牌，请求结束必须 reset
def bind_request_id(request_id: str):
    return REQUEST_ID.set(request_id)


# 做什么：恢复上一个请求编号，避免后台任务沿用别人的编号
# 入参：bind_request_id 返回的令牌
# 返回：无
def release_request_id(token) -> None:
    REQUEST_ID.reset(token)


# 做什么：初始化全局日志（只做一次，重复调用不会重复挂 handler）
# 入参：app_env 取 "dev" 或 "prod"，用来决定日志级别
# 返回：无（副作用是配置根 logger）
def setup_logging(app_env: str) -> None:
    root_logger = logging.getLogger()

    # 幂等保护：应用可能因为重载、测试多次调用，重复添加会出现一条日志打印好几遍
    if root_logger.handlers:
        return

    level = LOG_LEVEL_BY_ENV.get(app_env, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RequestIdFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))

    root_logger.addHandler(handler)
    root_logger.setLevel(level)


# 做什么：拿到某个模块的 logger
# 入参：name 一般直接传 __name__，日志里就能看出是哪个文件打的
# 返回：logging.Logger 实例
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
