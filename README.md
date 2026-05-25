# Fengxuan Agent Backend

基于 `FastAPI + LangChain(>=1.0) + LangGraph` 的智能体后端项目，支持多用户鉴权、模型管理、多轮对话、提示词模板、知识库检索（RAG）与系统状态查询。

## 后端代码说明（推荐阅读）

从架构、请求链路、各模块职责到「加功能时改哪些文件」，见 **[docs/BACKEND_GUIDE.md](docs/BACKEND_GUIDE.md)**。

## 技术栈

- Python 3.10+
- FastAPI
- LangChain 1.x
- LangGraph
- SQLAlchemy 2.x + Alembic
- MySQL
- Chroma
- Redis（可选）
- Poetry

## 快速开始

1. 安装依赖

```bash
poetry install
```

2. 配置环境变量

```bash
cp .env.example .env
```

3. 初始化数据库（先确保 MySQL 已创建对应数据库）

```bash
poetry run alembic upgrade head
```

4. 启动服务

```bash
poetry run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 接口模块

- `auth`: 注册/登录/刷新令牌/当前用户
- `models`: 模型配置 CRUD（API/Ollama/Remote）
- `sessions`: 会话管理与消息分页
- `chat`: 对话入口（流式与非流式）
- `prompts`: 提示词模板 CRUD
- `knowledge-bases`: 知识库 CRUD 与文本入库
- `system`: 系统状态、运行环境快照（无密钥）
- `mcp`: MCP 能力查询与服务端配置 CRUD
- `agent-tools`: 工具目录与内置工具开关

## 开发命令

```bash
poetry run pytest
poetry run ruff check .
poetry run mypy app
```

## 目录结构

```text
app/
  api/
  agents/
  core/
  db/
  integrations/
  models/
  repositories/
  schemas/
  services/
  utils/
alembic/
docs/
tests/
```
