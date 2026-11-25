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
- 参数：`--username`（可选，默认 `admin`），指定管理员账号 A；`--password`（可选）A 的密码；`--test-password`（可选）`accept_b` / `accept_c` 的密码。不传密码时从终端 `getpass` 读取。
- 前置条件：`127.0.0.1:8081` 已运行当前后端并连接项目 MySQL。健康检查、四条指定路由及 A 的 `users:assign_role` 必须通过。A 需持有 `admin` 角色和完整四项权限。
- 可否重复执行：可以。`accept_b` 和 `accept_c` 已存在时复用，已停用时通过 API 启用。成功跑完会撤销本轮验证用角色并停用两人。中途失败时按表格核对最后完成的步骤和最终状态。
- 执行方式：在仓库根目录运行 `uv run --no-sync python scripts/acceptance_stage2.py --username admin`；无法打开交互终端时追加 `--password` 与 `--test-password`。
- 注意：默认在真实终端用 `getpass` 输入 A 和两个验收账号的密码；新建验收账号时输入新密码并自行保存，复用时输入原密码。使用专用的 `accept_b`、`accept_c`，脚本会实际改变其角色和状态。脚本通过 API 操作，缺路由或权限时停止。 [报告模板](验收/阶段二.md)

### scripts/acceptance_stage3.py

- 用途：通过真实 HTTP 自检阶段三个人知识库链路：建库、上传纯文本、切片偏移与覆盖、引用详情、探针检索、无关问题零命中、删文档、删库与会话清库。
- 参数：`--username` 必填；`--password` 可选，不填则交互输入；`--base-url` 默认 `http://127.0.0.1:8081`；`--keep` 保留临时知识库供页面手验。
- 前置条件：8081 已运行当前后端并连接项目 MySQL，迁移为 `af4cfc254421`，`knowledge_chunks` 已有 ngram 全文索引。健康检查或登录失败时退出 `2`。
- 可否重复执行：可以。每次用带时间戳的临时库名，结束时删除；中途失败也会在收尾阶段删除（`--keep` 除外）。
- 执行方式：`uv run --no-sync python scripts/acceptance_stage3.py --username admin`。
- 注意：脚本不调用模型，因此不覆盖对话引用与原文抽屉的页面表现，那两项按报告里的「页面点击清单」人工核对。 [报告模板](验收/阶段三.md)

### scripts/corpus_import.py

- 用途：把指定目录中的 UTF-8 `.txt` 和 `.md` 批量上传为知识库文档。
- 参数：`--username`、`--password`、`--dir` 必填。`--base-url` 默认 `http://127.0.0.1:8081`。`--kb-name` 默认「语料库」，`--base-id` 可直接指定现有库，`--dry-run` 只检查本地并输出计划。
- 前置条件：真实导入需要可登录的服务和账号。预览只读取语料目录。
- 可否重复执行：可以。库内已有同标题文档会跳过。按目录一层扫描，不支持的扩展名会列出并停止整批导入。
- 执行方式：`uv run --no-sync python scripts/corpus_import.py --dir $corpusDir --username admin --password $password`。

### scripts/search_eval.py

- 用途：用固定问题评测检索结果，按轮次追加 Markdown 表格和汇总。
- 参数：`--username`、`--password`、`--base-id` 必填。`--base-url` 默认 `http://127.0.0.1:8081`，`--questions` 默认 `scripts/eval_questions.txt`，`--top-k` 默认 `5`，`--threshold-note` 默认 `ratio=0.5 / abs=0`，`--out` 默认 `docs/评测/检索质量.md`。
- 前置条件：目标库属于登录账号，语料和问题文件已准备好。
- 可否重复执行：可以。每轮追加一张表，标题带阈值说明。旧版服务缺少候选、丢弃字段或问题文件仍有盲测占位时退出。
- 执行方式：`uv run --no-sync python scripts/search_eval.py --base-id $baseId --username admin --password $password`。 [检索质量报告](评测/检索质量.md)

### scripts/eval_questions.txt

- 用途：评测默认问题模板。每行是 `问题` 或 `问题|期望命中的文档标题`，空行和 `#` 注释行可用于分组说明。
- 前置条件：使用五条示例时按报告准备对应的仓库文档，标题使用不含扩展名的文件名。

### scripts/eval_blind_questions.txt

- 用途：保留十条待填占位与跨语料盲测规则。由出题人复制模板、先写题、再核对期望标题。
- 执行方式：填好后用 `scripts/search_eval.py --questions $blindQuestions` 指定题目文件。 [盲测流程](评测/检索质量.md#盲测轮次)

### scripts/knowledge_http.py

- 用途：两个开发期脚本共用的标准库 HTTP 模块，封装登录、认证和分页。
- 执行方式：由两个脚本导入，作为同目录模块保留。

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


### scripts/rebuild_vector_index.py

- 用途：删除并重建 Qdrant 向量集合，把数据库中有效切片重新向量化，用于首次建索引、换模型或修复同步失败。
- 参数：无
- 前置条件：`fengxuan-qdrant` 容器已启动；`.env` 中 `VECTOR_ENABLED=true`；`EMBEDDING_DIM` 与新模型一致。
- 可否重复执行：可以。每次都会删除旧集合，跑完以数据库为准重建。
- 执行方式（在项目根目录）：

```powershell
uv run python scripts/rebuild_vector_index.py
```

- 注意：首次运行会下载模型，国内默认走 `EMBEDDING_HF_ENDPOINT` 镜像；集合名由 `QDRANT_COLLECTION` 决定，不要和别的项目共用容器。

## 五、每轮交付时的脚本清点

交付时固定报三行，让"有没有留下垃圾"这件事可见：

- 新增：[新建了哪些脚本，登记在哪一节]
- 删除：[删掉了哪些临时脚本，删之前它是干什么的]
- 保留：[为什么这个脚本值得长期留着]
