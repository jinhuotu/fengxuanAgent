# 预备知识（阶段 0）

面向 Python/Web 初学者。完成本章后再读项目代码，可减少「看不懂 `Depends`」的挫败感。

## 1. Python 必会（按顺序学）

| 序号 | 主题 | 你要能做什么 | 在本项目中的例子 |
|------|------|--------------|------------------|
| 1 | 变量与类型 | 声明 `name: str = "hi"` | 到处都是类型标注 |
| 2 | list / dict | 增删查改、遍历 | `WorkflowState` 本质是 dict |
| 3 | 函数 | `def foo(x: int) -> str:` | 每个 `*_service.py` 都是函数集合 |
| 4 | 类 | `class User:`、`self` | `app/models/*.py` 的 ORM |
| 5 | import | `from app.xxx import yyy` | 包名 `app` 即项目根下的目录 |
| 6 | 异常 | `try/except` | `chat_service` 捕获数据库错误 |
| 7 | 可选类型 | `str \| None` | 模板、知识库可为空 |

异步 `async def`：先知道「存在即可」；`main.py` 的 `lifespan` 会用到，聊天主路径多数是同步。

## 2. Web / API 必会

- **HTTP 方法**：GET 查询、POST 提交 body
- **状态码**：200 成功、401 未登录、404 不存在、422 参数错误、500 服务器错误
- **JSON**：请求体 `{"message": "你好"}`，响应体 `{"code": 0, "data": {...}}`
- **Bearer Token**：Header `Authorization: Bearer <access_token>`

## 3. 数据库必会

- **表 / 行 / 主键**：`users.id` 唯一标识用户
- **外键**：`chat_sessions.user_id` 指向 `users.id`
- **CRUD**：Create Read Update Delete
- **迁移**：改表结构后执行 `poetry run alembic upgrade head`

## 4. 与本项目强相关的 4 个概念

### 4.1 依赖注入（Depends）

```python
def chat(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ...
```

FastAPI 在调用 `chat` 前自动执行 `get_db()`、`get_current_user()`，把结果传进来。

### 4.2 ORM（SQLAlchemy）

Python 类 `ChatMessage` 对应表 `chat_messages`；`db.add(...)` 相当于 INSERT。

### 4.3 Schema（Pydantic）

`ChatRequest` 定义 API 入参有哪些字段、类型是什么；校验失败自动 422。

### 4.4 Tool Calling

大模型不直接执行代码，而是返回「请调用 `get_current_time`」；后端执行工具再把结果喂回模型。

## 5. 推荐迷你练习（不写进本项目）

1. 新建 `hello_fastapi.py`，`GET /hello` 返回 `{"msg":"hi"}`，用 uvicorn 跑起来。
2. 用 SQLite + SQLAlchemy 插入一行、再 `query` 查出来。

完成后再进入 [LEARNING_ENV_SETUP.md](LEARNING_ENV_SETUP.md)。
