"""错误层的冒烟测试：验证"业务错误"和"系统错误"都返回统一结构，且不泄露细节。

为什么现在就要有测试：异常处理器是全局的，写错了不会有明显报错，
只在出问题时表现异常。用两条用例把它钉住，比肉眼检查可靠。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    CODE_INTERNAL_ERROR,
    CODE_INVALID_CREDENTIALS,
    INTERNAL_ERROR_MESSAGE,
    InvalidCredentialsError,
    register_exception_handlers,
)


# 做什么：造一个临时应用，挂上全局异常处理器和两个会抛错的路由
# 入参：无
# 返回：配置好的 FastAPI 实例
def build_test_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def raise_business_error() -> None:
        raise InvalidCredentialsError()

    @app.get("/crash")
    def raise_unexpected_error() -> None:
        raise ValueError("这是一条不该出现在响应里的内部细节")

    return app


def test_business_error_returns_unified_body() -> None:
    client = TestClient(build_test_app(), raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == CODE_INVALID_CREDENTIALS
    assert body["message"] == "用户名或密码错误"
    assert body["data"] is None


def test_unexpected_error_hides_internal_detail() -> None:
    client = TestClient(build_test_app(), raise_server_exceptions=False)
    response = client.get("/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == CODE_INTERNAL_ERROR
    assert body["message"] == INTERNAL_ERROR_MESSAGE
    # 最关键的断言：内部细节不能出现在响应里
    assert "内部细节" not in body["message"]
