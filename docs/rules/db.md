# 数据库与缓存规则（docs/rules/db.md）

> 阶段一、阶段二必读。技术栈：MySQL 8 + SQLAlchemy 2.0 + PyMySQL（同步）。

## 一、连接与配置

- 连接串、池大小、超时全部写在 `core/config.py`，从 `.env` 读取，禁止硬编码。
- 连接串必须带 `charset=utf8mb4`，否则中文和 emoji 会乱码或截断。
- 建库字符集 `utf8mb4`、排序规则 `utf8mb4_unicode_ci`，时区统一 `+08:00`。
- 连接池参数：`pool_size=5`、`max_overflow=10`、`pool_recycle=1800`，
  必须开 `pool_pre_ping=True`（防 MySQL 8 小时断连后第一次请求报错）。

## 二、写库规则

- 一律用 SQLAlchemy 2.0 写法（`select()` / `session.execute()`），
  禁止 `session.query()` 旧风格，禁止同一个文件里两种混写。
- **禁止用 f-string 拼 SQL**，参数一律绑定传入（防注入，也确实好读）。
- 事务边界在 service 层：`with session.begin()`，**api 层永远不 commit**。
- 批量写入超过 100 条用 `bulk_save_objects` 或分批提交，别一条条循环 insert。
- 迁移文件一旦执行并提交，禁止修改，只能新增。

## 三、表设计约定（每张表保持同一风格）

- 表名复数小写下划线：`users`、`knowledge_documents`、`chat_sessions`。
- 每张表必备四列：`id`（BIGINT 自增主键）、`created_at`、`updated_at`、`is_deleted`（TINYINT 默认 0，软删除）。
- 关联字段统一 `xxx_id`（`user_id`、`session_id`），**不建物理外键**，在 service 层校验存在性。
- 状态、类型字段用 TINYINT + 常量类（`class UserStatus`），禁止裸数字 1/2/3。
- 金额用 `DECIMAL(12,2)`，禁止 FLOAT/DOUBLE；时间用 `DATETIME`，不要用字符串存时间。
- 需要存 JSON 的用 MySQL 原生 `JSON` 类型，不要塞一段字符串。
- 常用查询条件建索引，命名 `idx_表名_字段名`；唯一约束建在数据库层（`uk_表名_字段名`），不要只在代码里 if 判断。

## 四、查询规则

- 列表查询**必须分页**，默认 `limit=20`，必须设上限（最大 100），禁止无 limit 的 `select()` 全表返回。
- 禁止在循环里查数据库（N+1），需要关联数据时一次性查出来再在内存里组装。
- 单条查询用 `session.scalar()` / `session.scalars().first()`，不要查出来再 `[0]`。
- 删除一律软删除（置 `is_deleted=1`），查询默认带 `is_deleted=0` 条件。

## 五、缓存（**阶段三才启用，现在不要动**）

- 缓存读写封装在 `utils/cache.py`，业务代码里不直接调 Redis 客户端。
- key 格式 `fengxuan:{模块}:{对象id}`；**必须设 TTL，禁止无过期时间的写入**。
- 接 Redis 之前，先说明缓存什么、为什么需要、失效策略是什么，我同意后再接。
