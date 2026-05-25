# 阶段 4～5：内置工具与知识库 RAG

## 阶段 4：内置工具

| 文件 | 工具名 |
|------|--------|
| `app/services/tools/system_tools.py` | `get_system_status` |
| `app/services/tools/knowledge_tools.py` | `retrieve_context` |
| `app/services/tools/time_tools.py` | `get_current_time` |
| `app/services/tools/runtime_tools.py` | `get_python_runtime` |
| `app/services/tools/echo_tools.py` | **`get_echo`（学习示例）** |
| `app/services/tools/builtin_registry.py` | 注册表与 `build_builtin_langchain_tools` |

### 新增工具的标准步骤

1. `BUILTIN_TOOL_SPECS` 增加 spec  
2. `*_tools.py` 实现 `build_xxx_tool()`  
3. `build_builtin_langchain_tools` 增加分支  
4. 更新 `intent_node` 关键词与 `execute_tools_node` 系统提示  

### 毕业练习 `get_echo`（已实现）

对 Agent 说：「请用回显工具 echo 这句话：你好世界」  
期望：`tools_used` 含 `get_echo`，回答引用工具返回内容。

单测：`tests/test_echo_tool.py`（不依赖数据库）。

## 阶段 5：知识库 RAG

| 文件 | 作用 |
|------|------|
| `app/api/v1/knowledge_bases.py` | CRUD + ingest |
| `app/services/knowledge_base_service.py` | 业务 |
| `app/integrations/vector_store.py` | Chroma 切块与检索 |

### 动手：RAG 练习

1. `POST /api/v1/knowledge-bases` 创建 KB  
2. `POST .../ingest` 写入「公司暗号：菠萝888」  
3. `POST /api/v1/chat` 带 `kb_id`，问「暗号是什么」  

下一步：[LEARNING_PHASE_05_MCP_TESTS.md](LEARNING_PHASE_05_MCP_TESTS.md)
