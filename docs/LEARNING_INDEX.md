# 锋煊 Agent 精读学习索引

按顺序阅读下列文档，配合 [BACKEND_GUIDE.md](BACKEND_GUIDE.md) 与源码。

| 阶段 | 文档 | 内容 |
|------|------|------|
| 预备 | [LEARNING_PREREQUISITES.md](LEARNING_PREREQUISITES.md) | Python / HTTP / SQL 基础 |
| 环境 | [LEARNING_ENV_SETUP.md](LEARNING_ENV_SETUP.md) | Poetry、MySQL、Swagger 走通 |
| 1 | [LEARNING_PHASE_01_HTTP.md](LEARNING_PHASE_01_HTTP.md) | main、路由、鉴权 |
| 2 | [LEARNING_PHASE_02_DB.md](LEARNING_PHASE_02_DB.md) | ORM、Alembic |
| 3 | [LEARNING_PHASE_03_CHAT.md](LEARNING_PHASE_03_CHAT.md) | 对话主链路、LangGraph |
| 3+ | [LEARNING_CHAT_API.md](LEARNING_CHAT_API.md) | **`chat.py` 逐行精读** |
| 4～5 | [LEARNING_PHASE_04_TOOLS_RAG.md](LEARNING_PHASE_04_TOOLS_RAG.md) | 内置工具、`get_echo`、RAG |
| 6～8 | [LEARNING_PHASE_05_MCP_TESTS.md](LEARNING_PHASE_05_MCP_TESTS.md) | 提示词、MCP、pytest |

## 仓库内已落地的学习改动

- `GET /ping` — 验证路由修改
- `chat_once` 日志 — 观察轮次
- `get_echo` 内置工具 — 毕业练习
- `runner.py` — 修复 `WorkflowState` import
- 测试：`tests/test_ping.py`、`tests/test_echo_tool.py`
