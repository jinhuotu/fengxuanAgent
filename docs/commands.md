# 命令与依赖（docs/commands.md）

> 命令的唯一来源。跑之前从这里取，**不要猜**。改了环境就把这里同步改掉。
> 本项目用 **uv** 管理依赖，不再用 pip 直接装包。

## 一、环境事实

- Python：3.12.5（`D:\environment\python\python12`）
- 依赖管理：**uv 0.13.9**（winget 安装）
- 虚拟环境：`.venv`，**由 uv 维护，日常不需要手动激活**
- 数据库 / 缓存：**Docker**（`docker-compose.yml`），MySQL 映射 3307、Redis 映射 6380
- 索引源：清华 PyPI 镜像，写在 `pyproject.toml` 的 `[[tool.uv.index]]`
- 项目路径：`F:\code\python\fengxuanAgent`，服务端口 8000

## 二、日常命令（全部以 uv 开头，不用先 Activate）

```powershell
# 按 uv.lock 把依赖装齐（最常用；改完 pyproject 也跑这个）
uv sync

# 启动开发服务（改代码自动重启）
uv run uvicorn app.main:app --reload --port 8000

# 启动后自检：浏览器打开 http://127.0.0.1:8000/docs 能看到接口文档即成功

# 跑测试
uv run pytest -q

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

## 二之二、容器化基础设施（MySQL / Redis）

数据库和缓存都跑在 Docker 里（`docker-compose.yml`），不用本机装的 MySQL。

```powershell
# 只起数据库（阶段一、二用这个）
docker compose up -d mysql

# 到阶段三需要缓存时再单独起
docker compose up -d redis

# 看状态，等 STATUS 出现 (healthy) 才算真的可用，通常要 40 秒左右
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

端口约定：容器里的 3306 映射到本机 **3307**，Redis 的 6379 映射到 **6380**。
原因是本机 3306 和 6379 已被本地安装的服务占用，另一个项目又占了 13306 / 26379，
本项目单独用一对端口，互不干扰。所以 `.env` 里 `DB_PORT` 必须是 3307。

## 二之三、数据库迁移（alembic）

改了 `models/` 里的表结构，必须走这三步，不要手写 SQL 直接改表。

```powershell
# 1. 生成迁移文件（对比模型和数据库现状，把差异写成脚本）
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
   （你的项目在 F 盘、缓存在 C 盘）。不影响使用，想消掉警告就设 `UV_LINK_MODE=copy`。

## 六、部署时（Dockerfile 里怎么用，等部署阶段再落地）

```dockerfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev          # --frozen：绝不重新解析，只用锁文件
COPY app ./app
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 七、常见报错

| 报错最后一行 | 大概率原因 |
|---|---|
| `No solution found when resolving dependencies` | 依赖版本互相冲突，看它列出的冲突对 |
| `requires-python` 不匹配 | `.python-version` 与依赖要求不一致 |
| `Access denied for user` | MySQL 账号/密码/库名错，或 host 不是 127.0.0.1 |
| `cryptography is required` | 漏装 cryptography（MySQL 8 认证需要） |
| `ModuleNotFoundError: No module named 'app'` | 没在项目根目录启动 |
| `alembic.util.exc.CommandError` | `alembic/env.py` 没指向 models 的 Base |
