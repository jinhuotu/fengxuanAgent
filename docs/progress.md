# 项目进度与待定项（docs/progress.md）

> 本文件是**唯一允许频繁改动**的文档，但每次是"覆盖重写"，不是往后追加。
> 超过 30 行就说明该清理了：已完成的步骤删掉，只留当前状态、下一步和踩坑。
> 开工第一件事读本文件；每轮结束把状态更新回来。

## 一、当前状态（2026-11-21）

- 阶段一后端功能已实现。`POST /auth/login`、`GET /auth/me`、`POST /users` 已挂载，登录失败锁定已接入。前端进度仍记录完整浏览器验收待完成。
- 阶段二已有 RBAC 四张表和种子脚本。用户分配角色、接口按权限码鉴权仍待实现。`POST /users` 当前检查 `users.is_admin`。
- 源码迁移链为 `f71b1c2caccf`（建 users）→ `a743e8871d18`（加管理员标记）→ `b5f0c2d9e8a7`（补中文注释）→ `01619bd6e169`（建 RBAC 表）。目标库实际版本和注释以实查为准。
- 新库初始化已修复。`create_admin.py` 为新账号写入 `is_admin=True`，隔离测试覆盖登录、当前用户和建用户。目标 MySQL 库和浏览器仍待验收，见 [脚本说明](scripts.md)。
- 待确认数据库差异。[数据库规范](rules/db.md) 禁止物理外键，现有 RBAC 关联表使用级联外键。建用户服务在服务层显式提交，规范要求使用 `with session.begin()`。处理方案待确认。
- 既有表使用 `INT` 主键，未普遍实现规范中的软删除字段。`users.status` 是字符串，连接池回收时间为 3600 秒，规范写的是 1800 秒。以上差异仍待确认。

## 二、阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 登录（管理员建用户、登录、JWT 校验、/auth/me） | 功能已实现，完整浏览器验收仍待记录 |
| 阶段二 | RBAC 权限（角色、权限、接口鉴权、多账号验证） | 建表和种子脚本已完成，接口鉴权待接入 |
| 阶段三 | 对话 + 知识库（模型调用、SSE、检索） | 未开始 |
| 阶段四 | 多智能体协作 | 未开始 |

## 三、阶段一历史实现与验收记录

以下数据库状态和测试结果记录的是各步骤当时的环境。

1. ~~安装依赖~~ **已完成**：uv 管理，`uv.lock` 已生成，`.venv` 内 41 个包导入验证通过。
2. ~~建阶段一目录骨架~~ **已完成**：`app/` 七个目录 + `tests/` + `scripts/`，`__init__.py` 已放（`scripts/` 除外）。
3. ~~建 `.env.example` + `core/config.py`~~ **已完成**：九字段模板；`Settings` 无默认值的 `db_password`/`jwt_secret`；
   `get_settings()` 加 `lru_cache`；正向取值与缺值报错两条验收都通过。
4. ~~建 `core/errors.py`、`core/logging.py`~~ **已完成**：业务异常三类 + 三个全局处理器；日志一次初始化、幂等；`tests/test_errors.py` 2 条用例通过。
5. ~~写 `db/session.py`（引擎 + `get_db` 依赖），连上 MySQL 执行 `SELECT 1`~~ **已完成**：
   `select 1` = 1、`database()` = fengxuan_agent、`version()` = 8.4.13、字符集 utf8mb4、池 size 5。
6. ~~跑通 `GET /health`，确认 uvicorn 能起、`/docs` 能打开~~ **已完成**：`/health` 200 返回
   `{"code":0,"message":"成功","data":{"status":"ok","database":"ok"}}`，`/docs` 与 `/openapi.json` 均 200。
   期间踩到一个真实错误：`setup_logging()` 忘了传 `app_env`，启动直接 TypeError，已修。
7. ~~初始化 alembic，建 `users` 表，生成并执行迁移~~ **已完成**：`alembic init alembic`；
   `db/base.py` 定 `Base` 与约束命名规则；`models/user.py` 七列；autogenerate 生成 `f71b1c2caccf`；
   `upgrade head` 后 `describe users` 七列齐全、`username` 为 UNI；`downgrade -1` 再 `upgrade head` 往返成功。
8. ~~`core/security.py`（bcrypt 哈希 + JWT 签发/验签）~~ **已完成**：四个函数
   `hash_password` / `verify_password` / `create_access_token` / `decode_access_token`；
   `tests/test_security.py` 六条用例，全套 `8 passed`。实测哈希 60 字符、`$2b$12$` 前缀、token 三段、能解回 user_id。
9. ~~`scripts/create_admin.py`（一次性脚本造首个管理员）~~ **已完成**：`--username` / `--display-name`，密码交互式输入、
   两次确认、最短 8 位；先查重再插入（重跑不改数据）。实测插入成功、哈希前缀 `$2b$12$`、`status=active`、
   `created_at` 由数据库生成；验证用的 `smoke_admin` 已删除，`users` 表当前 0 行，等我自己建账号。
10. ~~`POST /auth/login`（校验密码 + 签发 token）+ "取当前用户"依赖 + `GET /me`~~ **已完成**：
    4 小块全部落地——`schemas/auth.py`（三个模型）、`services/auth.py`（`authenticate_user` / `login` /
    `get_active_user` / `resolve_current_user`）、`api/v1/auth.py`（登录与 `/me`，`main.py` 已挂路由）、
    `api/deps.py`（`HTTPBearer` 取头 + 转交 service）。
    实测五条路径：登录成功 200、密码错 401+1001、带 token `/me` 200、无 token 401+1002、
    坏 token 401+1002、停用账号 403+1003（登录与 `/me` 都是 403）；`pytest` 仍 8 条全绿。

11. ~~补做：管理员身份列 `users.is_admin`~~ **已完成**：`models/user.py` 加 `Boolean` 列（`default=False`、
    `server_default=text("0")`），`UserInfo` 与前端 `types/api.ts` 同步加 `is_admin`；
    `autogenerate` 生成 `a743e8871d18`，手工补了一句数据回填
    `UPDATE users SET is_admin = 1 WHERE username = 'admin'`（理由：新列默认 0，不回填就没人能调建用户接口）。
    验证：`describe users` 8 列、`TINYINT` 类型；`admin` 行 `is_admin=True`；`pytest 8 passed`；`alembic current = a743e8871d18 (head)`。

12. ~~补做：表和列的中文注释~~ **已完成**：`models/user.py` 8 列全带 `comment` + `__table_args__` 表注释；
    迁移 `b5f0c2d9e8a7` **手写**（autogenerate 不比对 comment，只会生成空迁移）；
    用 `ALTER TABLE ... MODIFY COLUMN` 照 `SHOW CREATE TABLE` 原定义抄一遍再加 COMMENT，
    不用 `op.alter_column`，因为它可能丢 `AUTO_INCREMENT` / 默认值 / 字符集。
    验证：`SHOW FULL COLUMNS` 8 条中文注释齐全，`Type`/`Null`/`Key`/`Default`/`Extra` 与改前一致；
    `SHOW CREATE TABLE` 末尾出现 `COMMENT='用户表：…'`；`pytest 8 passed`。
    唯一差异：`varchar` 从只显示 `COLLATE` 变成显式带 `CHARACTER SET utf8mb4`，语义相同。
    规则同步：`AGENTS.md` 第三节、`learning-contract.md` 检查清单、`architecture.md` 的 `models/` 职责各加一条。

13. ~~管理员建用户 `POST /users`~~ **已完成**：新增三个文件 —— `schemas/user.py`（`UserCreate`，密码 8-128 位）、
    `services/user.py`（`create_user`：查重 → 哈希 → 插入 → 撞唯一索引时 `rollback` 转 409 → `refresh` 读回时间戳）、
    `api/v1/users.py`（`POST /users`，依赖 `get_current_admin`）；
    `deps.py` 加 `get_current_admin`（先 `get_current_user`，再判 `is_admin`）；
    `errors.py` 加 `CODE_USERNAME_TAKEN=1005`（409）与 `CODE_PERMISSION_DENIED=2001`（403，按文件顶部 2000 段约定）。
    实测六条：管理员建用户 200 + `is_admin=False`；同名 409/1005；5 位密码 422/1000；无 token 401/1002；
    普通用户调用 403/2001；新账号用新密码登录 200/0。临时账号已清理，`users` 表只剩 `admin`。
    前端暂无建用户页面，接口先用 `/docs` 或脚本调。

## 阶段二进度

14. ~~第 1 步：建 RBAC 四张表~~ **已完成**：`models/rbac.py` 四个模型（`Role` / `Permission` / `UserRole` /
    `RolePermission`），每个表带表注释、每个列带中文 `comment`；
    `models/__init__.py` 已登记；迁移 `01619bd6e169` 由 autogenerate 生成并 `upgrade head`。
    结构验证：`roles.code`、`permissions.code` 各有唯一索引；`user_roles` 有 `UNI(user_id, role_id)`、
    `ix_user_roles_role_id`、两条 `ON DELETE CASCADE` 外键；`role_permissions` 有 `UNI(role_id, permission_id)`、
    `ix_role_permissions_permission_id`、两条 `ON DELETE CASCADE` 外键。
    建表时四张表为空，`users.is_admin` 未动，`pytest 8 passed`，`compileall` 通过。

15. ~~第 2 步：种子数据~~ **已完成**：`scripts/seed_rbac.py`，三个权限点（`users:create` / `users:disable` / `users:list`）、
    两个角色（`admin` 挂三项、`user` 不挂）；每个权限点、角色、授权关系都先按 code 查再插，
    重跑不重复、也不重置授权时间；全脚本只在末尾 commit 一次，中途失败不落半条。
    实测连跑两次：第一次新增 3 权限 + 2 角色 + 3 条授权，第二次零新增；
    行数 `roles=2` / `permissions=3` / `role_permissions=3` / `user_roles=0`。
    已登记 `docs/scripts.md` 第二节，部署顺序补了第 5 步。

## 四、阶段一已定方案（已拍板，按此执行）

1. **token：JWT**，`PyJWT` 实现，HS256 签名，有效期 **30 分钟**，阶段一不做 refresh token。
   副作用先接受：登出后旧 token 在过期前仍然有效；阶段三上 Redis 时加"登出黑名单"补这个洞。
   Payload 只放 `sub`（用户 id）、`exp`、`iat`，**不放密码、手机号、角色名**。
2. **统一响应结构：确认**，按 `docs/architecture.md` 第三节执行（`code`/`message`/`data`，分页固定四字段）。
3. **登录失败锁定：先内存实现**，同一账号连续失败 5 次锁 10 分钟，进程内字典 + 时间戳。
   阶段三迁 Redis 时再改，届时正好体会"重启就丢、多进程不共享"。
4. **使用者范围：我自己测试 + 会创建其他人员并分配权限做测试**。
   推论：登录必须支持多账号，阶段二必须至少有两个角色（管理员 / 普通用户）用来验证 403。
5. **前端来源：`http://127.0.0.1:5176` 与 `http://localhost:5176`**，写进 `CORS_ORIGINS`。
6. **注册：不做公开注册**，改为"管理员创建用户"接口（需要管理员 token 才能调）。
   users 表保留 `status` 字段，管理员可停用账号。

## 五、阶段一必须守住的两个细节

- 登录失败返回给客户端的提示要统一为"用户名或密码错误"，**不要区分是账号不存在还是密码错**（防探测账号）。
- 首个管理员通过一次性脚本初始化。当前脚本授权问题见本页当前状态和 [脚本说明](scripts.md)。

## 六、踩坑记录（第一次出现的错误先记这里，重复 2 次才升级进 AGENTS.md）

格式：`日期 | 现象 | 原因 | 怎么解决的`

- 2026-11-19 | 用管道给 `scripts/create_admin.py` 喂密码时进程挂住不动 | `getpass` 在非交互终端不读标准输入，`msvcrt` 一直等键盘 | 该脚本只能人工在真实终端执行；自动化验证改为直接调用 `create_admin()` 函数
- 2026-11-19 | VS Code 终端报"无法将 uv 项识别为 cmdlet"，重启 VS Code 后仍报 | winget 装 uv 时改了用户 PATH，但终端进程用的是启动那一刻拷贝的旧值，重启没拿到新值 | 建 `~\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`，新终端启动时按需把 uv 目录补进 PATH；全新 shell 验证 `uv 0.13.9` 可用
- 2026-11-19 | 跑 pytest 出现 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` | Starlette 1.10 起 TestClient 推荐 httpx2，当前版本仍兼容 | 暂不处理，测试能跑通；等它真报错再换 httpx2，现在换会牵动 dev 依赖
- 2026-11-20 | 调 `db_session.scalar_one_or_none(...)` 报 `AttributeError: 'Session' object has no attribute` | `one_or_none` 在 `ScalarResult` 上，不在 `Session` 上；写法和 `scalar` 不是一回事 | 改成 `db_session.scalars(select(...)).one_or_none()`，真跑一次才发现
- 2026-11-20 | 模型加了 `comment`，autogenerate 却生成一份空迁移 | Alembic 的 autogenerate 默认不比对 comment，只比对列/索引/约束的形状 | 改注释的迁移一律手写，用 `op.execute` 写 `ALTER ... MODIFY COLUMN`
- 2026-11-20 | 差点用 `op.alter_column` 改注释 | MySQL 改注释走 `MODIFY COLUMN`，会重写整列定义，漏参数就丢 `AUTO_INCREMENT`、默认值、字符集 | 照 `SHOW CREATE TABLE` 抄完整定义再补 `COMMENT`，改完用 `SHOW FULL COLUMNS` 逐列核对
- 2026-11-20 | 第一版 RBAC 迁移漏了 `user_roles.role_id` 的索引 | 联合唯一索引 `(user_id, role_id)` 的最左列是 `user_id`，按角色反查走不到它；模型里忘写 `index=True` | 补 `index=True` 后重生成迁移；重生成前先删掉那份没执行过的迁移，否则两份迁移会同时以 `b5f0c2d9e8a7` 为父，形成分叉
- 2026-11-20 | 验证脚本查不到接口刚创建的用户，报 `NoResultFound` | MySQL 默认隔离级别 REPEATABLE READ，同一会话第一次查询就开了事务，之后看不到别的连接提交的新行 | 脚本里查询前先 `db.commit()` 结束旧事务，下一次查询才读得到

## 七、关键决策记录（追加三行：日期 / 决定了什么 / 为什么）

- 2026-11-19 | 技术栈 FastAPI + SQLAlchemy 2.0 同步 + MySQL 8 | 体量小、同步写法更易读，异步复杂度暂不承担
- 2026-11-19 | AGENTS.md 拆为宪法 + 5 份专项文档 | 单文件 327 行，状态类内容会过期，按阶段生效的规则提前占上下文
- 2026-11-19 | token 用 JWT，30 分钟，不做 refresh | 无需存储即可验签，先把签发/验签/过期这条链路学完整；黑名单留到阶段三
- 2026-11-19 | 密码哈希直接用 bcrypt，不用 passlib | passlib 1.7.4 与 bcrypt 4.1+ 有已知兼容告警，多一层封装反而多一个坑
- 2026-11-19 | 不做公开注册，改管理员建用户 | 只有内部测试使用，避免暴露注册入口和重复的校验逻辑
- 2026-11-20 | 管理员身份先用 `users.is_admin` 布尔列，不提前上角色表 | 阶段一只需要回答"这个人能不能建用户"；角色表留到阶段二，届时把这一列迁成角色关联
- 2026-11-20 | 越权返 403 + code 2001，用户名重复返 409 + 1005 | 错误码按 `errors.py` 顶部分段（2000-2999 权限类）；409 表示请求合法但与服务器现状冲突，前端可提示换个名字
- 2026-11-20 | 多角色冲突取并集：任一角色持有该权限点即放行 | 判定写成"查存不存在"，SQL join 只要命中一行就算过，天然就是并集，不需要算交集或定优先级
- 2026-11-20 | 权限点命名统一 `资源:动作`，全小写，资源用复数 | `users:create`、`users:disable`、`knowledge:read`；固定格式才能在代码里写死字符串并做唯一索引，避免 `user:create` 和 `users.create` 混用
- 2026-11-19 | 依赖改用 uv（pyproject.toml + uv.lock），废弃 requirements.txt | 锁文件跨平台记录 wheel，Windows 开发 / Linux 部署一致；比 pip 快且能顺手管虚拟环境
- 2026-11-19 | 前端独立项目 `F:\code\python\fengxuanAgent-web`（Vue3 + TS + Vite + Element Plus，端口 5176） | 前后端分离，各自依赖互不影响；跨域用 Vite 代理 `/api` → 8000，后端 CORS 白名单作第二道保险
- 2026-11-19 | 登录响应字段定为 `{ access_token, token_type, expires_in }` | 前端 `src/types/api.ts` 已按此定义，后端第 10 步必须照此实现，否则联调对不上
- 2026-11-19 | 数据库与缓存放 Docker（`docker-compose.yml`），MySQL 映射 3307、Redis 映射 6380 | 本机 3306/6379 已被本地服务占用、另一套容器占 13306/26379，单独开一对端口互不干扰；容器化也和服务器保持一致
- 2026-11-19 | 索引源固定为清华镜像，写进 pyproject.toml | 本机 pip 配置里混着多个镜像，不显式固定会导致锁文件里的源随机变化
- 2026-11-20 | 后端路由前缀 `/auth`，前端 `/api` 由 Vite 代理去掉 | 浏览器看到同源请求，后端路径不带 /api，两边各自干净；改前缀必须同时改 vite.config.ts
- 2026-11-20 | 登录 service 收原始 `username` / `password`，不收整个 `LoginRequest` | 保持 service 不被 HTTP 形状绑住，脚本或别的入口也能复用
- 2026-11-20 | 失败锁定返回 429 + 1004；账号不存在也计数，账号停用不计数；锁定期内正确密码也拒 | 前端能分清"密码错"和"被锁"；不存在的也计数，防止被用来探测账号是否存在

- 日期 2026-11-21
- 决定按源码同步接口、阶段状态、迁移和初始化说明，保留历史验收记录。
- 原因是代码实现、目标库状态和用户验收分别需要不同证据。

- 日期 2026-11-21
- 决定在创建首个管理员时显式写入 `is_admin=True`。
- 原因是未设置该字段时会沿用普通用户默认值，登录后调用建用户接口会返回 403。
