# 项目进度与待定项（docs/progress.md）

> 本文件是**唯一允许频繁改动**的文档，但每次是"覆盖重写"，不是往后追加。
> 超过 30 行就说明该清理了：已完成的步骤删掉，只留当前状态、下一步和踩坑。
> 开工第一件事读本文件；每轮结束把状态更新回来。

## 一、当前状态（2026-11-19）

- 阶段：**阶段一 登录 · 进行中，第 7 步已完成，下一步第 8 步（`core/security.py`：bcrypt 哈希 + JWT 签发/验签）**
- 依赖管理：**uv 0.13.9**；`pyproject.toml` + `uv.lock` 已生成，41 个包已装入 `.venv`
- 已具备：`config.py`（九字段）、`errors.py`（三类业务异常 + 三个全局处理器）、`logging.py`（幂等）、
  `db/session.py`（唯一连接池 + `get_db`）、`utils/response.py`（`success()`）、`main.py`（CORS + `/health` 200 已通）、
  `db/base.py`（`Base` + 约束命名规则）、`models/user.py`（`users` 表）、`alembic/`（迁移 `f71b1c2caccf` 已执行）、
  `tests/test_errors.py`（2 条用例通过）
- 数据库：**Docker 容器 `fengxuan-mysql` healthy，127.0.0.1:3307 → 3306**；`users` 表 7 列、`username` 唯一索引；
  `downgrade -1` → `upgrade head` 往返验证通过
- 下一步：第 8 步，先 `core/security.py`（bcrypt 哈希 + JWT 签发/验签），再 `scripts/create_admin.py` 造首个管理员，
  最后才做 `POST /auth/login`

## 二、阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 登录（管理员建用户、登录、JWT 校验、/me） | 进行中 |
| 阶段二 | RBAC 权限（角色、权限、接口鉴权、多账号验证） | 未开始 |
| 阶段三 | 对话 + 知识库（模型调用、SSE、检索） | 未开始 |
| 阶段四 | 多智能体协作 | 未开始 |

## 三、阶段一开工顺序（做完一步就覆盖更新，不要留旧步骤）

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
8. **下一步**：`core/security.py`（bcrypt 哈希 + JWT 签发/验签）→ `scripts/create_admin.py` → `POST /auth/login`。

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
- 首次启动需要有一个管理员账号：用一次性脚本 `scripts/create_admin.py` 创建，**不通过接口、不走自动初始化**。

## 六、踩坑记录（第一次出现的错误先记这里，重复 2 次才升级进 AGENTS.md）

格式：`日期 | 现象 | 原因 | 怎么解决的`

- 2026-11-19 | VS Code 终端报"无法将 uv 项识别为 cmdlet"，重启 VS Code 后仍报 | winget 装 uv 时改了用户 PATH，但终端进程用的是启动那一刻拷贝的旧值，重启没拿到新值 | 建 `~\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`，新终端启动时按需把 uv 目录补进 PATH；全新 shell 验证 `uv 0.13.9` 可用
- 2026-11-19 | 跑 pytest 出现 `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` | Starlette 1.10 起 TestClient 推荐 httpx2，当前版本仍兼容 | 暂不处理，测试能跑通；等它真报错再换 httpx2，现在换会牵动 dev 依赖

## 七、关键决策记录（追加三行：日期 / 决定了什么 / 为什么）

- 2026-11-19 | 技术栈 FastAPI + SQLAlchemy 2.0 同步 + MySQL 8 | 体量小、同步写法更易读，异步复杂度暂不承担
- 2026-11-19 | AGENTS.md 拆为宪法 + 5 份专项文档 | 单文件 327 行，状态类内容会过期，按阶段生效的规则提前占上下文
- 2026-11-19 | token 用 JWT，30 分钟，不做 refresh | 无需存储即可验签，先把签发/验签/过期这条链路学完整；黑名单留到阶段三
- 2026-11-19 | 密码哈希直接用 bcrypt，不用 passlib | passlib 1.7.4 与 bcrypt 4.1+ 有已知兼容告警，多一层封装反而多一个坑
- 2026-11-19 | 不做公开注册，改管理员建用户 | 只有内部测试使用，避免暴露注册入口和重复的校验逻辑
- 2026-11-19 | 依赖改用 uv（pyproject.toml + uv.lock），废弃 requirements.txt | 锁文件跨平台记录 wheel，Windows 开发 / Linux 部署一致；比 pip 快且能顺手管虚拟环境
- 2026-11-19 | 数据库与缓存放 Docker（`docker-compose.yml`），MySQL 映射 3307、Redis 映射 6380 | 本机 3306/6379 已被本地服务占用、另一套容器占 13306/26379，单独开一对端口互不干扰；容器化也和服务器保持一致
- 2026-11-19 | 索引源固定为清华镜像，写进 pyproject.toml | 本机 pip 配置里混着多个镜像，不显式固定会导致锁文件里的源随机变化
