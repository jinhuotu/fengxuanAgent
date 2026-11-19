"""统一响应结构。

所有成功响应都从这里拼，保证 code / message / data 三个键永远在。
前端只要读 data，不用逐个接口判断这次返回的是哪种形状。
失败响应不在这里拼，由 core/errors.py 的全局处理器负责，两类各管一边。
"""

from typing import Any

# 0 表示成功。业务失败用非 0，规则写在 core/errors.py 顶部
CODE_SUCCESS = 0


# 做什么：拼一个成功响应
# 入参：data 是任意可序列化的内容；message 是给人看的提示
# 返回：code / message / data 三键字典
def success(data: Any = None, message: str = "成功") -> dict[str, Any]:
    return {"code": CODE_SUCCESS, "message": message, "data": data}
