# 阶段 6～8：提示词 / 会话 / 模型、MCP、测试

## 阶段 6：提示词、会话、模型

| 模块 | API 文件 | Service |
|------|----------|---------|
| 提示词 | `app/api/v1/prompts.py` | `app/services/prompt_service.py` |
| 会话 | `app/api/v1/sessions.py` | （多在路由内 + ORM） |
| 模型 | `app/api/v1/models.py` | `app/services/model_service.py` |

`render_prompt` 只替换 `{{question}}`、`{{context}}`，不是 Jinja2。

**练习**：创建模板「你是严谨的助手。用户问题：{{question}}」，聊天时传 `template_id`。

## 阶段 7：MCP（可后置）

| 文件 | 作用 |
|------|------|
| `app/api/v1/mcp.py` | 服务器配置 API |
| `app/services/mcp_runtime_service.py` | stdio 子进程 |
| `app/services/mcp_gateway_service.py` | 调用网关 |
| `app/services/tools/mcp_tools.py` | 动态 LangChain 工具 |

建议在阶段 3 完全理解后再读。

## 阶段 8：测试

```bash
poetry run pytest
poetry run ruff check .
```

| 测试文件 | 覆盖 |
|----------|------|
| `tests/test_health.py` | `/health` |
| `tests/test_ping.py` | `/ping`（学习） |
| `tests/test_echo_tool.py` | `get_echo` 工具（学习） |
| `tests/test_prompt_render.py` | 提示词渲染 |

## 能力自检清单

- [ ] Swagger 走通：注册 → 登录 → 模型 → 会话 → 聊天  
- [ ] 能口述 `POST /chat` 到 `chat_messages` 的路径  
- [ ] 改过 system_prompt 并看到效果  
- [ ] `get_echo` 出现在 `tools_used`  
- [ ] 知识库 ingest + 检索问答  
- [ ] 知道改表要走 Alembic  
- [ ] 跑通 `pytest`  

架构总览仍见 [BACKEND_GUIDE.md](BACKEND_GUIDE.md)。
