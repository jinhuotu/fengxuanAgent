# 架构与目录（docs/architecture.md）

> 只在"要新建文件、要新增接口"时读。AGENTS.md 里的三条分层边界优先级高于本文件。

## 一、目录骨架

```
fengxuanAgent/                        ✔ 已建 / □ 未建
├─ AGENTS.md                          ✔ 宪法：只放永久有效的硬约束（上限 80 行）
├─ pyproject.toml                     ✔ 依赖声明（uv 唯一来源）
├─ uv.lock                            ✔ 依赖锁定，必须提交
├─ .python-version                    ✔ 3.12
├─ .gitignore                         ✔ 排除 .venv 与 .env
├─ .env.example                       ✔ 九字段模板，密码与密钥留空
├─ docs/
│  ├─ progress.md                     ✔ 当前进度 / 下一步 / 待拍板 / 踩坑（每次都读）
│  ├─ architecture.md                 ✔ 本文件：目录、分层、统一结构
│  ├─ commands.md                     ✔ 命令的唯一来源（uv 写法）
│  ├─ learning-contract.md            ✔ 边写边学的流程与验收清单
│  ├─ 沟通格式约定.md                   ✔ 讲解结构与表达约定
│  ├─ scripts.md                      ✔ 脚本清单：用途/参数/前置/部署顺序
│  ├─ 术语表.md                       ✔ 概念 / 一句话解释 / 用在哪
│  ├─ 新手导航.md                     ✔ 看不懂时的第一页：是什么/有什么/下一步
│  ├─ 验收/                           ✔ 每阶段一份清单与自检结果，阶段二起
│  ├─ archive/                        ✔ 历史版本，只读不改
│  └─ rules/
│     ├─ db.md                        ✔ 阶段一、二读
│     ├─ llm.md                       ✔ 阶段三起读
│     └─ agents.md                    □ 阶段四起才建
├─ app/                               ✔ 阶段一已建
│  ├─ main.py                         ✔ 应用装配：CORS + 异常处理器 + auth/users 路由 + /health
│  ├─ core/                           ✔ config.py ✔ / errors.py ✔ / logging.py ✔ / security.py ✔（bcrypt + JWT）
│  ├─ api/                            ✔ deps.py（取当前用户、按权限码提供守卫）
│  │  └─ v1/                          ✔ auth.py（登录、当前用户、权限清单、本人改密、退出登录）、users.py（分页列表、创建用户、追加/撤销角色、切换状态）
│  ├─ db/                             ✔ session.py ✔ / base.py ✔（Base + 约束命名规则）
│  ├─ services/                       ✔ auth.py ✔ login_guard.py ✔ user.py（创建与分页查询）✔ password.py ✔ rbac.py（绑定与鉴权）
│  ├─ models/                         ✔ user.py ✔（users 表，含登录令牌版本号）/ rbac.py ✔（roles、permissions、user_roles、role_permissions）/ __init__.py 负责登记模型
│  ├─ schemas/                        ✔ auth.py ✔ user.py ✔（创建用户、列表分页参数和公开响应）✔ rbac.py（角色绑定请求）
│  ├─ utils/                          ✔ response.py ✔（success 统一响应，准入：被两个以上模块使用）
│  ├─ llm/                            ✔ client.py（流式 HTTP）+ prompts/chat.txt（系统提示词）
│  ├─ rag/                            □ 阶段三建：切分、检索
│  └─ agents/                         □ 阶段四建：角色与编排
├─ tests/                             ✔ test_errors.py、test_security.py、test_create_admin.py、test_password.py、test_rbac.py、test_user_list.py（分页查询）
├─ scripts/                           ✔ create_admin.py ✔（造首个管理员）/ seed_rbac.py ✔（初始化角色与权限点）/ acceptance_stage2.py ✔（阶段二真实 HTTP 自检）
└─ alembic/                           ✔ env.py 已接 Base 与配置；versions/ 迁移链 f71b1c2caccf → a743e8871d18 → b5f0c2d9e8a7 → 01619bd6e169 → 24c713a60f20 → 7e9204b6d815 → 9c4d16b8e2f1（当前 head）
```

Python 包通过 `__init__.py` 声明包边界。`models/__init__.py` 还会导入表模型，供 Alembic 读取元数据。
**按阶段逐步建，没到的阶段不建空目录**：`llm` / `rag` / `agents` 到对应阶段再建。

## 二、分层职责（细版）

| 层 | 只做什么 | 绝对不做 |
|---|---|---|
| `api/` | 收参数、Pydantic 校验、调 service、包统一响应 | 写 SQL、写业务判断、commit、直接 import models |
| `services/` | 业务逻辑、事务边界、跨表校验 | 碰 HTTP 请求对象、拼 URL、写响应格式 |
| `models/` | 表结构、字段、索引、关系、中文注释（comment 进 DDL，给不看代码的人看） | 业务方法、查询逻辑 |
| `schemas/` | 请求/响应结构 | 直接返回 ORM 对象给 api 层 |
| `core/` | 配置、异常、日志、安全工具 | 业务逻辑 |
| `utils/` | 无业务含义的通用函数（如 cache 封装） | 与业务表耦合 |

登录请求的链路，完整说明见 [新手导航](新手导航.md)。

```
客户端 → POST /auth/login → LoginRequest 校验 → get_db 提供会话
      → auth service 查 users 表、验密码、签发 JWT → 统一响应
```

`GET /auth/me` 通过 `get_current_user` 取 Bearer token，由 service 验签并查询账号状态。
`GET /auth/me/permissions` 复用当前用户依赖，调用 RBAC 服务返回 `{permissions: [...]}`；仅查本人，多角色权限去重并按权限码排序，无权限返回空数组，不按 is_admin 放行。无凭证或账号失效由既有身份依赖拒绝。
登录签发 JWT 时带用户当前令牌版本 `ver`，认证时与 `users.token_version` 比较。版本不同时返回 `401/1002`。迁移前签发且未过期的无版本令牌按版本 0 校验。
`POST /auth/change-password` 校验凭证和旧密码后，在同一事务里更新密码和令牌版本。成功返回 `data: null`，旧密码错误为 `400/1006`。
`POST /auth/logout` 校验本人凭证，在事务里递增令牌版本并返回 `data: null`。该账号此前签发的令牌随即失效。
`POST /users` 通过 `require_user_creation_permission` 依赖，由 RBAC 服务检查 `users:create` 权限。`is_admin` 不替代角色授权；运行服务切换前须先确保现有管理员获得对应角色权限。
`GET /users` 检查 `users:list`，按 page/page_size 返回 items/total/page/page_size。每条含 id/username/display_name/status/roles，角色仅含 code/name。先分页用户，再批量查该页角色，无角色返回 []；普通页三次 SQL，空页跳过角色查询。
`POST /users/{user_id}/roles` 检查 `users:assign_role`，接收 `{role_code}`，返回 `{created}` 表示是否新增。追加本人角色返回 `403/2003`。鉴权会话与写入会话通过 `use_cache=False` 分开，由角色绑定服务管理写入事务。
`DELETE /users/{user_id}/roles/{role_code}` 同样检查 `users:assign_role`，返回 `data.removed`。撤销本人返回 `403/2003`。撤销 `admin` 前锁定角色行并统计其他启用管理员，数量为零时返回 `409/2004`。
`PATCH /users/{user_id}/status` 检查 `users:disable`，接收 `active` 或 `disabled`，返回 `data.changed` 和 `data.status`。拒绝操作本人和停用最后一名启用管理员。停用时在同一事务里递增令牌版本，启用时保留版本。
`GET /roles` 检查 `users:list`，在 `data.items` 中按角色代码排序返回 `code` 和 `name`。
`services/rbac.py` 的 `has_permission()` 用 EXISTS 查询按多角色并集判断，停用账号不放行。`ensure_permission()` 在无权限时抛出 `PermissionDeniedError`，由依赖调用，接口层不写业务判断。

## 三、统一响应结构（已确认，按此实现）

建议形态：

```json
{ "code": 0, "message": "成功", "data": { ... } }
```

- `code` 为 0 表示成功。1001 表示用户名或密码错误，1002 表示凭证无效，1004 表示账号被锁定。错误码以 [errors.py](../app/core/errors.py) 为准。
- `PATCH /users/{user_id}/status` 先检查 `users:disable` 权限。提交不支持的 `status` 值时，无权限返回 `403/2001`，有权限返回 `422/1000`。
- HTTP 状态码仍然要正确使用（401/403/404/422），不要所有请求都返回 200。
- 分页响应的 `data` 固定为：

```json
{ "items": [], "total": 0, "page": 1, "page_size": 20 }
```

- 异常统一在 `core/errors.py` 定义业务异常类，在 `main.py` 注册全局异常处理器，**不在每个接口里 try/except 兜底**。

## 四、CORS（前端 Vue3 联调需要）

`main.py` 从 `core/config.py` 读取允许的前端来源。默认值为 `http://127.0.0.1:5176` 和 `http://localhost:5176`，可通过 `CORS_ORIGINS` 配置。
开发时，Vite 将 `/api` 请求转发到 `http://127.0.0.1:8000`，并移除路径中的 `/api` 前缀。

## 五、AI 对话

新增 `api/v1/chat.py`、`schemas/chat.py`、`models/chat.py`，路由挂载在 `main.py`。
服务分为 `chat_history.py` 的本人历史、`chat_turns.py` 的事务和占用、`chat_context.py` 的缓存、`chat_stream.py` 的流式编排。
`chat_recovery.py` 托管启动及周期过期恢复；在有界短事务中使用行锁和 SKIP LOCKED，应用关闭时等待当前批次结束。
`chat_capacity.py` 在发送事务的第一个查询中锁住固定准入记录，再校验全站有效占用；锁随接收事务结束释放，不陪伴模型流。全站默认 10，同用户跨会话可并行，同会话仍限 1。
`chat_requests.py` 按请求 UUID 查询本人问题和回答，计算最近失败轮次的重试资格；发送时在既有事务和会话行锁内再次校验 retry_of_request_id，不增加表或迁移。
第九项在 chat_history.get_session_state 中用本人行锁和既有恢复逻辑查询有效占用；会话与结果 DTO 公开历史版本/最新活动 UUID，供多标签页只读校准；状态和结果 GET 禁止缓存，无新表或模型调用。
`llm/tokens.py` 保守估算完整请求，`services/chat_budget.py` 按预算选择最近完整轮次；chat_context 缓存候选及裁剪标记，每次按当前问题重新生成计划，流式 context 事件只做提示。
`chat_prewarm.py` 管理每进程有界预热队列；main 生命周期启动/关闭队列，API 只传递队列引用。保存成功后非阻塞入队并结束 SSE，后台使用独立短会话和版本快照，不能影响成功状态。
`utils/cache.py` 封装 Redis，`utils/sse.py` 统一 SSE 和断线清理；模型网络请求只在 `llm/client.py`。
`core/chat_errors.py` 定义安全模型错误及历史标识/SSE 错误码对照；客户端按 HTTP、传输和协议错误分类，流式服务将分类保存到历史并返回固定文案。
MySQL 是完整历史主存储，Redis 只缓存带用户、会话和版本的有界上下文，故障回源。
新增迁移 `24c713a60f20` 接在 `01619bd6e169` 后面，只增加两张对话表。
迁移 `7e9204b6d815` 接在其后，增加 chat_generation_gates 固定记录和 chat_sessions 有效占用索引。
当前联调前端 5178、后端 8081。配置、接口、失败行为和完整文件清单见 [AI 对话说明](chat.md)。
生成中正文按配置间隔通过 chat_turns.save_partial_answer 在短事务中保存；状态仍为 generating，快照不更新成功历史版本。
tests/test_chat_partial_save.py 使用 tests/partial_save_worker.py 在专用子进程中跳过 finally 退出，验证临时数据库中的快照可恢复；不需要手动运行该测试辅助模块。
