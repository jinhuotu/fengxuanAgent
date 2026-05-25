# 阶段 1：入口与 HTTP 层

## 阅读顺序

| 顺序 | 文件 | 要回答的问题 |
|------|------|--------------|
| 1 | `app/main.py` | 应用如何创建？路由挂在哪里？ |
| 2 | `app/core/config.py` | `.env` 怎么变成 `Settings`？ |
| 3 | `app/api/router.py` | `/api/v1` 下有哪些子模块？ |
| 4 | `app/schemas/response.py` | 成功响应长什么样？ |
| 5 | `app/core/exceptions.py` | 错误如何变成 JSON？ |
| 6 | `app/core/deps.py` | 如何从 token 得到当前用户？ |
| 7 | `app/api/v1/auth.py` + `app/services/auth_service.py` | 路由如何调用 service？ |

## 已实现的学习改动

- **`GET /ping`**（`app/main.py`）：返回 `{"pong": true}`，用于确认你改代码后服务已重载。

## 动手练习

1. 浏览器访问 `http://127.0.0.1:8000/ping`。
2. 在 Swagger 用错误 token 调 `/api/v1/auth/me`，观察 401 的 body 结构。
3. 用笔记模板记录 `auth.py`：谁调用它？它调用谁？输入输出？

## 读懂标志

能说出：新增接口要改 **`schemas` → `api/v1/xxx.py` → `services`**，并在 `router.py` 注册。

下一步：[LEARNING_PHASE_02_DB.md](LEARNING_PHASE_02_DB.md)
