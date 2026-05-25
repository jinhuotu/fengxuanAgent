# 环境搭建与 Swagger 走通

## 1. 安装清单

- Python 3.10+
- [Poetry](https://python-poetry.org/)
- MySQL 8（创建数据库，名称与 `.env` 中 `MYSQL_DB` 一致，默认 `agent_backend`）
- （可选）Ollama 或 OpenAI 兼容 API，用于真实对话

## 2. 命令（Windows）

```powershell
cd f:\data\python\fengxuanAgent
poetry install
copy .env.example .env
# 编辑 .env：改 MYSQL_PASSWORD 等
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 3. 验证

| 检查项 | URL / 命令 | 期望 |
|--------|------------|------|
| 健康检查 | `GET http://127.0.0.1:8000/health` | `{"status":"ok",...}` |
| 学习用 ping | `GET http://127.0.0.1:8000/ping` | `{"pong":true}` |
| API 文档 | `http://127.0.0.1:8000/docs` | Swagger UI 可打开 |

## 4. Swagger 最小业务链

1. `POST /api/v1/auth/register` — 注册
2. `POST /api/v1/auth/login` — 复制 `access_token`
3. 右上角 **Authorize** → `Bearer <token>`
4. `POST /api/v1/models` — 创建模型（Ollama 或 API）
5. `POST /api/v1/sessions` — 创建会话
6. `POST /api/v1/chat` — 例如「现在几点了？」或「查一下系统状态」

响应 `data.tools_used` 中出现工具名，说明工具链路正常。

## 5. 常见问题

| 现象 | 处理 |
|------|------|
| 连接 MySQL 失败 | 检查 `.env` 主机、端口、密码、库是否已创建 |
| 503 tools_used_json | 执行 `poetry run alembic upgrade head` |
| 启动报 WorkflowState | 已修复 `runner.py` import，拉最新代码 |
| MCP 相关 warning | 不影响基础聊天；需要 MCP 时再 `poetry install` 确保 mcp 包可用 |

下一步：[LEARNING_PHASE_01_HTTP.md](LEARNING_PHASE_01_HTTP.md)
