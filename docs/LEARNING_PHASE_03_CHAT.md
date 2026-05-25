# 阶段 3：对话主链路（核心）

**正在读 `app/api/v1/chat.py`？** 请看更细的逐行说明：[LEARNING_CHAT_API.md](LEARNING_CHAT_API.md)。

## 一条消息的路径

```text
POST /api/v1/chat  (app/api/v1/chat.py)
  → chat_once        (app/services/chat_service.py)
  → run_chat_workflow (app/workflows/runner.py)
       intent → plan → execute_tools → answer
  → 写入 chat_messages
  → 返回 JSON 或 SSE
```

## 阅读顺序

### API → Service

- `app/api/v1/chat.py` — 校验归属、`stream` 时 SSE 切块
- `app/schemas/chat.py` — `ChatRequest` 字段
- `app/services/chat_service.py` — 历史、工具、`run_chat_workflow`

### LangGraph

- `app/workflows/state.py` — `WorkflowState` 各键含义
- `app/workflows/runner.py` — 图拓扑（已修复 `WorkflowState` import）
- `app/workflows/nodes/intent_node.py` — 关键词 → `need_tool`
- `app/workflows/nodes/plan_node.py` — `tool_plan` 列表
- `app/workflows/nodes/execute_tools_node.py` — **`create_agent`** + system_prompt
- `app/workflows/nodes/answer_node.py` — 最终答案与 `tools_used`

### 模型与工具

- `app/services/model_service.py`
- `app/integrations/model_adapters.py`
- `app/services/tools/builtin_registry.py`

## 已实现的学习改动

1. **`chat_once` 日志**：终端可见 `chat_once: session_id=... turn_count=...`
2. **`execute_tools_node` system_prompt**：增加「简短列表」与 `get_echo` 说明

## 动手练习

1. 发一条聊天，在终端找 `chat_once` 与 `WorkflowTrace` 日志。
2. Swagger 设 `stream: true`，在浏览器 Network 看 `meta` → `chunk` → `final`。
3. 修改 `execute_tools_node.py` 里一句提示，观察回答风格变化。

## 读懂标志

- 能解释 `tools_used` 从 `answer_node` / `tool_trace` 来
- 知道 `intent` 不阻止调用工具，只做观测

下一步：[LEARNING_PHASE_04_TOOLS_RAG.md](LEARNING_PHASE_04_TOOLS_RAG.md)
