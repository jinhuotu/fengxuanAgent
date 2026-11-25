"""验证日志的三件事：请求编号贯穿、访问日志成形、业务错误留痕。"""
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.chat_errors import ModelAuthenticationError
from app.core.errors import BusinessError, TokenInvalidError, register_exception_handlers
from app.core.logging import REQUEST_ID, RequestIdFormatter, bind_request_id, release_request_id
from app.main import log_requests
from app.services.chat_stream import log_model_failure

# 测试专用的知识库错误，语义与线上 4003 一致
class ChunkNotFoundError(BusinessError):
    code = 4003
    http_status = 404
    message = "切片不存在"


# 做什么：造一个挂上访问日志中间件的小应用
# 入参：无
# 返回：只有 /ping 的 FastAPI 实例
def build_ping_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(log_requests)

    @app.get("/ping")
    def ping(request: Request) -> dict:
        # 模拟鉴权依赖把用户编号放到请求上
        request.state.user_id = 7
        return {"ok": True}

    return app


# 做什么：核对访问日志带方法、路径、状态、耗时、用户编号与请求编号
# 入参：无
# 返回：无；日志字段缺失或响应头没有编号时断言失败
def test_access_log_carries_request_id_and_fields(caplog) -> None:
    client = TestClient(build_ping_app())
    with caplog.at_level(logging.INFO, logger="app.main"):
        response = client.get("/ping", headers={"X-Request-ID": "req-abc"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-abc"
    assert "请求完成 | GET /ping | status=200" in caplog.text
    assert "user_id=7" in caplog.text and "stream=False" in caplog.text
    assert "elapsed_ms=" in caplog.text


# 做什么：核对未带编号时自动生成，且请求结束后上下文回到默认值
# 入参：无
# 返回：无；编号重复或上下文残留上一条请求编号时断言失败
def test_access_log_generates_and_resets_request_id() -> None:
    client = TestClient(build_ping_app())
    first = client.get("/ping")
    second = client.get("/ping")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert REQUEST_ID.get() == "-"


# 做什么：核对格式化器把上下文里的编号补进日志前缀
# 入参：无
# 返回：无；前缀缺少编号时断言失败
def test_formatter_injects_request_id() -> None:
    formatter = RequestIdFormatter(fmt="%(levelname)s | %(request_id)s | %(message)s")
    record = logging.LogRecord("demo", logging.WARNING, __file__, 1, "出事了", None, None)
    token = bind_request_id("req-format")
    try:
        assert formatter.format(record) == "WARNING | req-format | 出事了"
    finally:
        release_request_id(token)


# 做什么：核对业务错误会留一条带码、路径和用户编号的记录
# 入参：无
# 返回：无；没有日志或缺少字段时断言失败
def test_business_error_is_logged(caplog) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing")
    def raise_business() -> None:
        raise ChunkNotFoundError()

    with caplog.at_level(logging.INFO, logger="app.core.errors"):
        TestClient(app).get("/missing", headers={"X-Request-ID": "req-missing"})
    assert "业务错误 | GET /missing | code=4003 | http=404" in caplog.text


# 做什么：核对高频噪声（过期令牌 1002）不写业务错误日志
# 入参：无
# 返回：无；噪声错误被记录时断言失败
def test_token_error_is_not_logged(caplog) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/token")
    def raise_token_error() -> None:
        raise TokenInvalidError()

    with caplog.at_level(logging.INFO, logger="app.core.errors"):
        TestClient(app).get("/token")
    assert "业务错误" not in caplog.text


# 做什么：核对模型错误单独记一条 WARNING，带错误分类而不是只混在 INFO 摘要里
# 入参：无
# 返回：无；级别、字段或混杂了问答内容时断言失败
def test_model_failure_logged_as_warning(caplog) -> None:
    turn = SimpleNamespace(user_id=3, session_id=11, request_id="req-model")
    with caplog.at_level(logging.WARNING, logger="app.services.chat_stream"):
        log_model_failure(turn, ModelAuthenticationError())
    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert "模型调用失败" in record.getMessage()
    assert "error_code=model_authentication_failed" in record.getMessage()
    assert "http=503" in record.getMessage()
