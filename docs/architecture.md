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
│  ├─ 术语表.md                       ✔ 概念 / 一句话解释 / 用在哪
│  ├─ 新手导航.md                     ✔ 看不懂时的第一页：是什么/有什么/下一步
│  ├─ archive/                        ✔ 历史版本，只读不改
│  └─ rules/
│     ├─ db.md                        ✔ 阶段一、二读
│     ├─ llm.md                       ✔ 阶段三起读
│     └─ agents.md                    □ 阶段四起才建
├─ app/                               ✔ 阶段一已建
│  ├─ main.py                         ✔ 应用装配：CORS + 异常处理器 + /health（第 6 步完成）
│  ├─ core/                           ✔ config.py ✔（Settings+get_settings）/ errors.py ✔ / logging.py ✔ / security.py □
│  ├─ api/v1/                         ✔ auth.py □（后续 users.py roles.py…）
│  ├─ db/                             ✔ session.py ✔ / base.py ✔（Base + 约束命名规则）
│  ├─ services/                       ✔ 业务逻辑，按模块一个文件
│  ├─ models/                         ✔ user.py ✔（users 表，7 列）/ __init__.py 负责登记模型
│  ├─ schemas/                        ✔ auth.py □
│  ├─ utils/                          ✔ response.py ✔（success 统一响应，准入：被两个以上模块使用）
│  ├─ llm/                            □ 阶段三建：client.py + prompts/
│  ├─ rag/                            □ 阶段三建：切分、检索
│  └─ agents/                         □ 阶段四建：角色与编排
├─ tests/                             ✔ api/ services/ 两个子目录 + test_errors.py（错误层冒烟测试）
├─ scripts/                           ✔ create_admin.py □
└─ alembic/                           ✔ env.py 已接 Base 与配置；versions/ 首个迁移 f71b1c2caccf
```

**每个目录下都有空的 `__init__.py`（`scripts/` 除外），用来固定包边界，让 uv、pytest、alembic 的路径解析一致。**
**按阶段逐步建，没到的阶段不建空目录**：`llm` / `rag` / `agents` 到对应阶段再建。

## 二、分层职责（细版）

| 层 | 只做什么 | 绝对不做 |
|---|---|---|
| `api/` | 收参数、Pydantic 校验、调 service、包统一响应 | 写 SQL、写业务判断、commit、直接 import models |
| `services/` | 业务逻辑、事务边界、跨表校验 | 碰 HTTP 请求对象、拼 URL、写响应格式 |
| `models/` | 表结构、字段、索引、关系 | 业务方法、查询逻辑 |
| `schemas/` | 请求/响应结构 | 直接返回 ORM 对象给 api 层 |
| `core/` | 配置、异常、日志、安全工具 | 业务逻辑 |
| `utils/` | 无业务含义的通用函数（如 cache 封装） | 与业务表耦合 |

一个请求的链路（阶段一跑通后补进 docs/新手导航.md）：

```
客户端 → uvicorn → 路由(api/v1/auth.py) → 依赖注入(取 token、取 db)
      → service(校验密码、签发 token) → models(读写 users 表) → 统一响应
```

## 三、统一响应结构（已确认，按此实现）

建议形态：

```json
{ "code": 0, "message": "成功", "data": { ... } }
```

- `code`：0 表示成功；业务失败用非 0 整数（如 1001 密码错误、1002 账号被锁定）。
- HTTP 状态码仍然要正确使用（401/403/404/422），不要所有请求都返回 200。
- 分页响应的 `data` 固定为：

```json
{ "items": [], "total": 0, "page": 1, "page_size": 20 }
```

- 异常统一在 `core/errors.py` 定义业务异常类，在 `main.py` 注册全局异常处理器，**不在每个接口里 try/except 兜底**。

## 四、CORS（前端 Vue3 联调需要）

阶段一就要在 `main.py` 配置 CORS，允许本地前端来源（如 `http://127.0.0.1:5173`）。
允许的来源写进 `core/config.py` 从 `.env` 读，**不要写 `allow_origins=["*"]` 图省事**。
