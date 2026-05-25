"""统一 API 响应模型。"""

from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None


def ok(data: Optional[T] = None, message: str = "success") -> APIResponse[T]:
    return APIResponse(code=0, message=message, data=data)
