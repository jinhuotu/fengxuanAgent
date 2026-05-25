"""应用级异常与全局处理器。"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError


class AppException(Exception):
    def __init__(self, message: str, code: int = 4000) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"code": exc.code, "message": exc.message, "data": None}
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail, "data": None},
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"code": 422, "message": str(exc), "data": None}
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=500, content={"code": 5001, "message": str(exc), "data": None}
        )
