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
- 内容：权限点 `users:create` / `users:disable` / `users:list`；角色 `admin`（挂全部三项）与 `user`（不挂任何权限点）。
- 执行方式（在项目根目录）：

```powershell
uv run python scripts/seed_rbac.py
```

- 脚本结束会记录每个角色当前拥有的权限。当前 `POST /users` 通过 `is_admin` 鉴权，角色权限查询尚未接入接口。

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
