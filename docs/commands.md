# 命令与依赖（docs/commands.md）

> 命令的唯一来源。跑之前从这里取，**不要猜**。改了环境就把这里同步改掉。
> 本项目用 **uv** 管理依赖，不再用 pip 直接装包。

## 一、环境事实

- Python：3.12.5（`D:\environment\python\python12`）
- 依赖管理：**uv 0.13.9**（winget 安装）
- 虚拟环境：`.venv`，**由 uv 维护，日常不需要手动激活**
- 数据库 / 缓存 / 向量库：**Docker**（`docker-compose.yml`），MySQL 映射 3307、Redis 映射 6380、Qdrant 映射 6335/6336
- 索引源：清华 PyPI 镜像，写在 `pyproject.toml` 的 `[[tool.uv.index]]`
- 项目路径：`D:\code\LLM\fengxuanAgent`，服务端口 8081
<!-- 历史端口：后端 8000。 -->

## 二、日常命令（全部以 uv 开头，不用先 Activate）

当前本机前端代理目标为 8081。重启联调后端时，先在原服务终端按 `Ctrl+C` 停止。

密码哈希需要 `PASSWORD_PEPPER`。部署时从密钥管理注入同一份独立随机密钥，
例如 32 个随机字节的 Base64 文本。服务和首管脚本使用同一份密钥，重启时继续复用。
本地可在 PowerShell 7.2 或更新版本的同一终端中隐藏输入并启动：

```powershell
$env:PASSWORD_PEPPER = Read-Host -MaskInput '输入已保存的 PASSWORD_PEPPER'
uv run --no-sync uvicorn app.main:app --reload --port 8081
```

登录和改密所提交的密码最多 128 个 UTF-8 字节。设置新密码时至少输入 8 个字符。

```powershell
# 按 uv.lock 把依赖装齐（最常用；改完 pyproject 也跑这个）
uv sync

# 启动开发服务（改代码自动重启）
uv run uvicorn app.main:app --reload --port 8081

# 启动后自检：浏览器打开 http://127.0.0.1:8081/docs 能看到接口文档即成功

# 日常隔离回归，自动加载测试配置，不读取 .env
uv run --no-sync python -m pytest -q

# 语法检查
uv run python -m compileall app

# 数据库迁移（改完 models 后）
uv run alembic revision --autogenerate -m "本次改动的说明"
uv run alembic upgrade head

# 看一眼临时脚本 / 单文件
uv run python scripts\xxx.py

# 加依赖（不要用 pip install）
uv add passlib          # 运行时依赖
uv add --dev ruff       # 只在开发用的依赖
uv remove passlib

# 只重新解析锁定文件（不动环境）
uv lock

# 生产环境安装（跳过 dev 组）
uv sync --frozen --no-dev

# 看依赖树 / 看装了哪些
uv tree
uv pip list
```

## 二之二、容器化基础设施（MySQL / Redis / Qdrant）

数据库、缓存和向量库都跑在 Docker 里（`docker-compose.yml`），不用本机装的 MySQL。

```powershell
# 只起数据库（阶段一、二用这个）
docker compose up -d mysql

# 到阶段三需要缓存时再单独起
docker compose up -d redis

# 启用向量检索（.env 里 VECTOR_ENABLED=true）时起；起完要跑一次 scripts/rebuild_vector_index.py
docker compose up -d qdrant

# 看状态，等 STATUS 出现 (healthy) 才算真的可用，MySQL 通常要 40 秒左右
docker compose ps

# 看启动日志（第一次启动出问题先看这里）
docker compose logs -f mysql

# 进容器里的 MySQL 客户端（输入 .env 里的 DB_PASSWORD）
docker exec -it fengxuan-mysql mysql -uroot -p

# 停止并删除容器，数据卷保留（下次 up 数据还在）
docker compose down

# 危险：连数据卷一起删，库里的数据全部丢失，只在确定重建时用
docker compose down -v
```

Qdrant 没有设密码，所以端口只绑 `127.0.0.1`，别的机器连不进来。它的数据卷 `fengxuan-qdrant-data` 是早先手动建的，`docker compose up` 会提示一句 `volume ... was not created by Docker Compose`，属预期，可忽略。

端口约定：容器里的 3306 映射到本机 **3307**，Redis 的 6379 映射到 **6380**。
原因是本机 3306 和 6379 已被本地安装的服务占用，另一个项目又占了 13306 / 26379，
本项目单独用一对端口，互不干扰。所以 `.env` 里 `DB_PORT` 必须是 3307。

登录保护使用现有 Redis 配置。设置 `REDIS_ENABLED=false` 时走进程内存。连接故障时也会回退，日志级别为 `ERROR`。
检查项目 Redis 的端口映射和响应：

```powershell
docker port fengxuan-redis 6379/tcp
docker exec fengxuan-redis redis-cli ping
```

## 二之三、数据库迁移（alembic）

改了 `models/` 里的表结构，必须走这三步，不要手写 SQL 直接改表。

```powershell
# 1. 生成迁移文件（新增表、字段等结构变更）
uv run alembic revision --autogenerate -m "给这次改动起个名字"

# 2. 打开 alembic/versions/ 下新生成的文件看一眼，确认它要做的事对
#    注意：autogenerate 检测不出"改名"，改字段名会被当成"删一列再加一列"

# 3. 执行迁移，让数据库真的变
uv run alembic upgrade head

# 回退一步（验证迁移可逆，改错了也能重来）
uv run alembic downgrade -1

# 看数据库当前停在哪个版本
uv run alembic current
```

连接串不写进 `alembic.ini`，由 `alembic/env.py` 从 `core/config.py` 取，密码只有 `.env` 一处来源。

新建表时，核对迁移里的表注释和每一列的中文 `comment`。仅补既有表注释时，参考 [users 注释迁移](../alembic/versions/b5f0c2d9e8a7_add_chinese_comments_to_users.py) 显式写入迁移。使用 `MODIFY COLUMN` 时，按目标库的原定义保留类型、默认值、可空性、自增和字符集等属性。

迁移后，在目标库用 `SHOW FULL COLUMNS FROM 表名` 核对列注释，用 `SHOW CREATE TABLE 表名` 核对表注释和其他结构属性。

## 二之四、创建第一个管理员

```powershell
uv run python scripts/create_admin.py --username admin
```

密码是交互式输入的，**必须在真实终端里跑**，不要用管道喂密码（`getpass` 在非交互终端不会读标准输入，进程会卡住）。
用户名已存在时脚本只打一行日志、不改任何数据，可以放心重跑。

脚本会给新账号设置 `is_admin=True`。建库顺序和接口验收见 [部署顺序](scripts.md#三部署到新服务器的顺序)。

## 三、四个文件的分工（记这一句就够）



| 文件 | 作用 | 是否提交 |
|---|---|---|
| `pyproject.toml` | 声明要什么（版本范围、依赖分组） | 提交 |
| `uv.lock` | 锁定实际装了什么（精确版本 + 哈希 + 源） | **必须提交** |
| `.python-version` | 声明用 3.12 | 提交 |
| `.venv/` | 真正的环境目录，可从锁文件随时重建 | **不提交** |

原则：**改依赖只改 `pyproject.toml`（用 `uv add`），装环境只读 `uv.lock`。**

## 四、当前锁定的关键版本（2026-11-19）

```
python 3.12.5        fastapi 0.164.0       starlette 1.10.0
sqlalchemy 2.1.9     pymysql 1.2.4         alembic 1.20.1
pydantic 2.14.3      pydantic-core 2.50.4  pydantic-settings 2.16.3
uvicorn 0.54.0       bcrypt 5.0.0          pyjwt 2.15.1
cryptography 50.0.2  pytest 9.2.0          httpx 0.28.1
```

共 41 个包（含传递依赖）。`uv.lock` 会同时记录 Windows 与 Linux 的 wheel 信息，
所以同一份锁文件在服务器和 Docker 里也能用。

## 五、网络问题排查（你机器上真实遇到过的）

1. uv **会读 pip 配置和 `PIP_*` 环境变量**，它们可能覆盖 `pyproject.toml` 里的源。
   症状：明明改了源，`uv.lock` 里记录的还是旧镜像。
   处理：清掉 `PIP_CONFIG_FILE`、`PIP_INDEX_URL`、`UV_INDEX_URL` 后重新 `uv lock`。
2. 报 `tunnel error` / `tcp connect error ... 10061`：系统代理指向了 `127.0.0.1:7890`
   但代理软件没开。开代理，或清空 `HTTP_PROXY` `HTTPS_PROXY` `ALL_PROXY`。
3. uv 的进度输出走 **stderr**，PowerShell 里 `2>&1` 会显示成 NativeCommandError，
   只要看到 `Resolved` / `Installed` 就是成功，不要被红字吓到。
4. 看到 `Failed to hardlink files ... UV_LINK_MODE=copy`：缓存和 `.venv` 不在同一个盘
   （你的项目在 D 盘、缓存在 C 盘）。不影响使用，想消掉警告就设 `UV_LINK_MODE=copy`。

## 六、部署时（Dockerfile 里怎么用，等部署阶段再落地）

AI 对话已批准运行依赖为 `redis>=5.2,<6`、`httpx==0.28.1`，锁定 Redis 客户端 5.3.1。
迁移、配置和当前 8081/5178 启动顺序见 [AI 对话说明](chat.md)。

```dockerfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev          # --frozen：绝不重新解析，只用锁文件
COPY app ./app
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
```

## 七、常见报错

全站并发控制新增迁移 `7e9204b6d815`，运行新代码前先由使用者执行 `uv run alembic upgrade head`。
下面只用于临时 `chat_verify` 隔离验证，不读取 `.env`，不使用项目 MySQL 容器或数据卷；容器退出自动清理自身测试数据。

```powershell
docker images mysql:8.4
docker run --detach --rm --name fengxuan-capacity-1123-verify -e MYSQL_ROOT_PASSWORD=chat-isolated-only -e MYSQL_DATABASE=chat_verify -p 127.0.0.1:13317:3306 mysql:8.4
docker exec fengxuan-capacity-1123-verify mysqladmin ping -uroot -pchat-isolated-only --silent
uv run --no-sync python -m pytest tests/mysql_chat_checks.py --chat-mysql-port 13317 -q
docker stop fengxuan-capacity-1123-verify
```

| 报错最后一行 | 大概率原因 |
|---|---|
| `No solution found when resolving dependencies` | 依赖版本互相冲突，看它列出的冲突对 |
| `requires-python` 不匹配 | `.python-version` 与依赖要求不一致 |
| `Access denied for user` | MySQL 账号/密码/库名错，或 host 不是 127.0.0.1 |
| `cryptography is required` | 漏装 cryptography（MySQL 8 认证需要） |
| `ModuleNotFoundError: No module named 'app'` | 没在项目根目录启动 |
| `alembic.util.exc.CommandError` | `alembic/env.py` 没指向 models 的 Base |
