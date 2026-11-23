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
│  ├─ archive/                        ✔ 历史版本，只读不改
│  └─ rules/
│     ├─ db.md                        ✔ 阶段一、二读
│     ├─ llm.md                       ✔ 阶段三起读
│     └─ agents.md                    □ 阶段四起才建
├─ app/                               ✔ 阶段一已建
│  ├─ main.py                         ✔ 应用装配：CORS + 异常处理器 + auth/users 路由 + /health
│  ├─ core/                           ✔ config.py ✔ / errors.py ✔ / logging.py ✔ / security.py ✔（bcrypt + JWT）
│  ├─ api/                            ✔ deps.py（取当前用户、检查管理员身份）
│  │  └─ v1/                          ✔ auth.py（登录和当前用户）、users.py（管理员建用户）
│  ├─ db/                             ✔ session.py ✔ / base.py ✔（Base + 约束命名规则）
│  ├─ services/                       ✔ auth.py ✔ login_guard.py ✔ user.py ✔
│  ├─ models/                         ✔ user.py ✔（users 表，8 列）/ rbac.py ✔（roles、permissions、user_roles、role_permissions）/ __init__.py 负责登记模型
│  ├─ schemas/                        ✔ auth.py ✔ user.py ✔
│  ├─ utils/                          ✔ response.py ✔（success 统一响应，准入：被两个以上模块使用）
│  ├─ llm/                            □ 阶段三建：client.py + prompts/
│  ├─ rag/                            □ 阶段三建：切分、检索
│  └─ agents/                         □ 阶段四建：角色与编排
├─ tests/                             ✔ test_errors.py（错误响应）、test_security.py（密码哈希和 JWT）
├─ scripts/                           ✔ create_admin.py ✔（造首个管理员）/ seed_rbac.py ✔（初始化角色与权限点）
└─ alembic/                           ✔ env.py 已接 Base 与配置；versions/ 迁移链 f71b1c2caccf → a743e8871d18 → b5f0c2d9e8a7 → 01619bd6e169
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
`POST /users` 再通过 `get_current_admin` 检查 `is_admin`。RBAC 表和种子数据已有对应代码，角色权限查询尚未接入接口。

## 三、统一响应结构（已确认，按此实现）

建议形态：

```json
{ "code": 0, "message": "成功", "data": { ... } }
```

- `code` 为 0 表示成功。1001 表示用户名或密码错误，1002 表示凭证无效，1004 表示账号被锁定。错误码以 [errors.py](../app/core/errors.py) 为准。
- HTTP 状态码仍然要正确使用（401/403/404/422），不要所有请求都返回 200。
- 分页响应的 `data` 固定为：

```json
{ "items": [], "total": 0, "page": 1, "page_size": 20 }
```

- 异常统一在 `core/errors.py` 定义业务异常类，在 `main.py` 注册全局异常处理器，**不在每个接口里 try/except 兜底**。

## 四、CORS（前端 Vue3 联调需要）

`main.py` 从 `core/config.py` 读取允许的前端来源。默认值为 `http://127.0.0.1:5176` 和 `http://localhost:5176`，可通过 `CORS_ORIGINS` 配置。
开发时，Vite 将 `/api` 请求转发到 `http://127.0.0.1:8000`，并移除路径中的 `/api` 前缀。
