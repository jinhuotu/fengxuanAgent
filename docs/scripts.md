# 脚本清单与使用说明（docs/scripts.md）

> 只登记"换台机器还需要再跑一次"的脚本。一次性调试脚本不登记，跑完即删。
> 每新增一个 scripts/ 下的文件，必须在本文件补一节；删除脚本时同步删节。
> 部署到新服务器时，按本文件第三节的顺序执行，不要凭记忆。

## 一、三类文件，先分清

| 类别 | 放在哪 | 生命周期 | 典型例子 |
|---|---|---|---|
| 回归测试 | `tests/` | 永久，随代码提交，每次 `pytest` 都跑 | `tests/test_security.py` |
| 运维脚本 | `scripts/` | 长期保留，必须登记到本文件 | `scripts/create_admin.py` |
| 临时验证 | 项目目录之外（系统临时目录） | 验完即删，不得进 git | 内存 SQLite 验证登录 |

判断标准只有一句：**换一台服务器、换一个人，还需不需要再跑一次。**
需要，进 `scripts/` 并登记；不需要，验完删掉。

## 二、现有脚本

### scripts/create_admin.py

- 用途：创建管理员账号并设置 `is_admin=True`。项目不做公开注册，首个管理员只能由这个脚本产生。
- 参数：`--username`（必填，唯一）；`--display-name`（可选，不填则前端显示用户名）。
- 前置条件：MySQL 容器已启动、`alembic upgrade head` 已执行、`.env` 里 `DB_PASSWORD` 已填。
- 密码输入：交互式两次输入，不回显；最短 8 位；不要用管道喂密码（`getpass` 会卡死）。
- 可否重复执行：可以。用户名已存在时只打日志、不改任何数据。
- 若旧版脚本已占用目标用户名且该账号没有管理员权限，可用尚未占用的用户名初始化管理员。
- 执行方式（在项目根目录）：

```powershell
uv run python scripts/create_admin.py --username admin
```

- 数据库连接密码含 `@ : / ?` 等字符时，当前连接串拼接需要先做 URL 编码。

### scripts/seed_rbac.py

- 用途：初始化 RBAC 的角色、权限点和角色授权关系。脚本运行后，用户与角色的绑定仍需单独完成。
- 参数：无
- 前置条件：MySQL 容器已启动、`alembic upgrade head` 已执行（四张 RBAC 表必须先存在）。
- 可否重复执行：可以。每个权限点、每个角色、每条授权关系都是先按 code 查再插，已存在的跳过；不会插重复，也不会重置绑定时间。
- 内容：权限点 `users:create`、`users:disable`、`users:list` 和 `users:assign_role`。角色 `admin` 挂全部四项，`user` 的权限清单为空。
- 执行方式（在项目根目录）：

```powershell
uv run python scripts/seed_rbac.py
```

- 已有环境重跑脚本后，已绑定 `admin` 角色的账号会获得 `users:assign_role`。首次部署需在初始化时完成首管的 `admin` 角色绑定。

### scripts/acceptance_stage2.py

- 用途：通过真实 HTTP 验证阶段二的授权、角色变更、停用和启用，输出可粘贴到验收报告的结果。
- 参数：`--username`（可选，默认 `admin`），指定管理员账号 A。
- 前置条件：`127.0.0.1:8081` 已运行当前后端并连接项目 MySQL。健康检查、四条指定路由及 A 的 `users:assign_role` 必须通过。A 需持有 `admin` 角色和完整四项权限。
- 可否重复执行：可以。`accept_b` 和 `accept_c` 已存在时复用，已停用时通过 API 启用。成功跑完会撤销本轮验证用角色并停用两人。中途失败时按表格核对最后完成的步骤和最终状态。
- 执行方式：在仓库根目录运行 `uv run --no-sync python scripts/acceptance_stage2.py --username admin`。
- 注意：在真实终端用 `getpass` 输入 A 和两个验收账号的密码。新建验收账号时输入新密码并自行保存，复用时输入原密码。使用专用的 `accept_b`、`accept_c`，脚本会实际改变其角色和状态。脚本通过 API 操作，缺路由或权限时停止。 [报告模板](验收/阶段二.md)

## 三、部署到新服务器的顺序

1. 装依赖：`uv sync --frozen --no-dev`
2. 起数据库：`docker compose up -d mysql`，等 `docker compose ps` 显示 healthy
3. 建表：`uv run alembic upgrade head`
4. 初始化首个管理员：`uv run python scripts/create_admin.py --username admin`
5. 初始化角色与权限：`uv run python scripts/seed_rbac.py`
6. 起服务：`uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000`
7. 自检：`GET /health` 返回 `database: ok`，`POST /auth/login` 能拿到 token。管理员验收还需检查 `GET /auth/me` 的 `is_admin` 和实际建用户结果。

先迁移建表，再运行初始化脚本。用新建账号登录后，确认 `GET /auth/me` 返回 `is_admin=true`。当前源码要求创建用户者持有 `users:create`；仅初始化管理员标记还不够，需经明确授权完成角色绑定后，再验证 `POST /users` 可创建普通账号。

## 四、新增脚本的登记模板

复制下面这段，把方括号换成实际内容：

```
### scripts/[文件名]

- 用途：[一句话说清它解决什么问题]
- 参数：[参数名 + 含义 + 是否必填]
- 前置条件：[需要先完成什么，比如迁移已执行]
- 可否重复执行：[可以 / 不可以，重复跑会发生什么]
- 执行方式：`[完整命令]`
- 注意：[容易踩的坑，没有就写"无"]
```

## 五、每轮交付时的脚本清点

交付时固定报三行，让"有没有留下垃圾"这件事可见：

- 新增：[新建了哪些脚本，登记在哪一节]
- 删除：[删掉了哪些临时脚本，删之前它是干什么的]
- 保留：[为什么这个脚本值得长期留着]
