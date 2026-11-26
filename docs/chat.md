# AI 对话实现与启用

## 当前范围

第三方 DeepSeek `deepseek-flash`，文本流式回答，MySQL 保存历史，Redis 按需缓存近期成功上下文。
所有正常登录用户可用；会话列表、历史、发送和中断都只允许本人访问，管理员也不例外。
同一用户可以在不同会话同时生成，切换或新建会话不会中断旧任务；同一会话一次只生成一个回答。关闭、刷新或离开整个对话页会中断本页面全部任务，部分回答在历史中标记中断。
本次没有加入向量库、知识库、工具调用或管理他人历史；会话删除只做本人软删，不提供恢复入口。

## 启用顺序

请由本机使用者自行配置 `LLM_API_KEY`。开发过程中未读取或修改 `.env`，没有索取真实 Key。
其他配置均由 `app/core/config.py` 管理，模型调用不会在前端发生。

| 配置 | 默认值 |
|---|---|
| LLM_BASE_URL | https://api.deepseek.com |
| LLM_MODEL | deepseek-flash |
| LLM_TIMEOUT_SECONDS | 60，整个生成的总时限 |
| LLM_MAX_RETRIES | 0，只允许建连阶段有限重试，最多 3 次 |
| LLM_MAX_OUTPUT_TOKENS | 2048 |
| REDIS_ENABLED | true，故障自动回源 MySQL |
| REDIS_HOST / REDIS_PORT / REDIS_DATABASE | 127.0.0.1 / 6380 / 0 |
| REDIS_PASSWORD | 空 |
| REDIS_TIMEOUT_SECONDS | 0.3 |
| CHAT_CACHE_TTL_SECONDS | 1800 |
| CHAT_CONTEXT_ROUNDS | 10，最多 50 |
| CHAT_CONTEXT_MAX_CHARACTERS | 12000，历史总字符上限 |
| CHAT_MAX_ANSWER_CHARACTERS | 50000，防止异常供应商无界输出 |
| CHAT_PARTIAL_SAVE_INTERVAL_SECONDS | 2，有新增正文时的保存间隔，范围 0.1–30 秒 |
| CHAT_RECOVERY_ENABLED | true，随服务启停的后台过期恢复开关 |
| CHAT_RECOVERY_INTERVAL_SECONDS | 30，每批结束后等待的秒数，范围 0.1–3600 |
| CHAT_RECOVERY_BATCH_SIZE | 100，每轮最大处理会话数，范围 1–500 |
| CHAT_GLOBAL_CONCURRENCY_LIMIT | 10，全站有效生成占用上限，至少 1，不设置用户总上限 |
| LLM_CONTEXT_WINDOW_TOKENS | 32768，应用采用的兼容预算窗口，不是自动探测的模型容量 |
| CHAT_INPUT_TOKEN_BUDGET | 24000，完整输入的保守估算预算 |
| CHAT_CONTEXT_SAFETY_TOKENS | 512，窗口内另行预留的安全空间 |
| CHAT_PREWARM_ENABLED | true，启用可选后台预热；Redis 停用时同样不创建队列 |
| CHAT_PREWARM_QUEUE_CAPACITY | 20，每进程待执行容量，不含正在执行的任务 |
| CHAT_PREWARM_WORKERS | 2，每进程后台工作任务数，范围 1–8 |
| CHAT_PREWARM_SHUTDOWN_SECONDS | 1，关闭时等待执行中预热的期限，范围 0.01–30 秒 |
| VECTOR_ENABLED | false，默认关闭；关闭时只用关键词检索，不加载模型也不连 Qdrant |
| VECTOR_RETRIEVAL_MODE | keyword，可选 vector 或 hybrid；仅 VECTOR_ENABLED=true 时生效 |
| QDRANT_URL | http://127.0.0.1:6335，本机 `fengxuan-qdrant` 容器（`docker compose up -d qdrant`） |
| QDRANT_COLLECTION | fengxuan_chunks，向量集合名 |
| EMBEDDING_MODEL | BAAI/bge-small-zh-v1.5，本地 ONNX 中文向量模型 |
| EMBEDDING_DIM | 512，必须与模型输出一致；换模型要同时重建索引 |
| EMBEDDING_CACHE_DIR | .cache/fastembed，模型缓存，已 git 忽略 |
| EMBEDDING_HF_ENDPOINT | https://hf-mirror.com，仅供首次下载模型使用 |
| VECTOR_MIN_SCORE_RATIO | 0.5，向量候选低于本次最高相似度一半时丢弃 |

首次部署先同步已获批准的依赖，再执行新增迁移。`24c713a60f20` 增加两张对话表；`7e9204b6d815` 增加 `chat_generation_gates` 固定准入锁记录，以及 `chat_sessions` 的有效占用索引，不修改已有消息。已部署旧对话版本时也须先升级到新迁移，再启动新代码。
启用向量检索时先起向量库：`docker compose up -d qdrant`（容器 `fengxuan-qdrant`，定义在 `docker-compose.yml`，只绑本机 6335/6336，重启后自动拉起），再执行一次 `scripts/rebuild_vector_index.py` 建集合；模型首次使用会下载到 `EMBEDDING_CACHE_DIR`，离线环境需提前准备缓存。
以下命令也已登记在 `docs/commands.md`，应在后端根目录执行：

```powershell
uv sync
docker compose up -d redis
uv run alembic upgrade head
uv run --no-sync uvicorn app.main:app --reload --port 8081
```

前端在 `D:\code\LLM\fengxuanAgent-web` 执行 `npm run dev`，当前实际端口 5178，代理后端 8081。
登录后首页有“AI 对话”入口。创建会话后即可发送，Ctrl + Enter 也可提交。
真实项目数据库迁移和真实付费模型请求未由本次测试代为执行，不能把隔离验证当成真实环境已上线。

## 一次请求的数据流

页面只提交当前问题和 UUID 请求编号，不提交用户编号、系统提示词或完整历史。
后端在独立短会话中验证 JWT，在新事务中先取得全站准入锁，再锁本人会话、校验去重和并发名额，保存问题和助手占位，结束事务。
上下文优先读取带版本的 Redis 历史候选；未命中或故障时查询 MySQL，只保留完整成功轮次，再根据系统提示词、当前问题及输出预留计算本次计划。
后端请求模型并逐段返回 SSE。结束时先保存结果，成功后尝试非阻塞提交预热，再发送 completed 并结束 SSE；不等待缓存预热。等待模型期间不持有数据库事务。
生成中有新增文字时按配置间隔开启短事务保存正文；没有新文字时不重复写库，等待下一片段期间由心跳检查保存时机。
前端结束后释放本地生成状态，再从主存储后台校准临时消息编号和持久化状态；用户可继续输入或发送下一问，旧历史响应不得覆盖新轮次。

## 接口

| 方法和路径 | 行为 |
|---|---|
| POST /chat/sessions | 创建本人会话 |
| GET /chat/sessions | 分页查询本人会话，最近更新在前 |
| GET /chat/sessions/{id}/messages | 分页历史，第一页是最新消息，每页内部按时间正序 |
| POST /chat/sessions/{id}/messages | `{request_id, content}`，SSE 流式生成 |
| POST /chat/sessions/{id}/requests/{request_id}/interrupt | 幂等中断本人指定请求 |
| GET /chat/sessions/{id}/requests/{request_id} | 按原 UUID 查询本人问题、回答、忙状态、最新活动 UUID 和重试资格，不调用模型 |
| GET /chat/sessions/{id}/state | 查询本人会话的历史版本和有效活动 UUID，恢复过期占用，不返回消息正文 |
| DELETE /chat/sessions/{id} | 删除本人一条会话：生成中拒绝，成功后消息与会话一起软删 |
| DELETE /chat/sessions | 清空本人全部会话：任一条在生成时整批拒绝，不做部分删除 |

普通接口沿用 `{code, message, data}`。身份失效 401，非本人或不存在的会话统一 404。
重复请求返回 409/3003，正在生成返回 409/3002，未配置 Key 返回 503/5002。
单删返回 `{session_id, deleted_messages}`，清空返回 `{deleted_sessions, deleted_messages}`。删除走 `is_deleted=1` 软删，同时清空活动请求和生成到期时间、历史版本加一，因此旧 Redis 候选缓存键不会再被该会话读到；消息行保留以满足请求去重唯一约束。删除不可恢复，没有回收站。
全站满额在 SSE 开始前返回 JSON `429/3004`，不写入本轮消息，不排队；前端保留问题供重发。准入锁记录失效返回 `503/3005`，缺表等数据库故障走既有安全错误处理，不绕过额度保护。
请求查询返回 `{request_id, question, answer, session_busy, can_retry, history_version, active_request_id}`，question/answer 沿用公开消息结构。未知或不完整轮次为 `404/3006`；非本人会话仍为既有 404，不返回其他用户内容。查询也会恢复已到期占用。会话列表、历史中的 session 和状态接口统一带 history_version、active_request_id、generation_expires_at；列表只展示有效占用，状态查询在短事务中恢复过期占用。状态和结果响应设置 Cache-Control: no-store，前端查询也禁用缓存。
问题去除首尾空白后不能为空，最多 4000 字符。分页默认 20 条，最多 100 条。
SSE 事件为 started、context、delta、ping、completed、error、interrupted。context 仅在历史被裁剪时发送，包含 message、retained_rounds、latest_round_omitted；不是错误或终结事件。事件字段见两端 chat 类型文件。
流已开始后使用 error 事件报告问题；不再尝试修改已发送的 HTTP 状态码。

## 历史、缓存和并发

会话保存用户归属、标题、历史版本、活动请求和生成到期时间；消息保存请求编号、角色、状态、正文和用量。
数据库唯一约束 `(session_id, request_id, role)` 防止重复落库，同一会话的行锁防止跨连接重复生成。
当前请求编号也是结束写入的凭证。旧请求即使迟到，也不能完成或清除新请求的占用。

全站准入固定记录由迁移创建，不允许在运行中删除或软删除。每个发送事务首先使用 `FOR UPDATE` 锁住该记录，再检查本人会话及全站有效占用，最后在同一事务内写入新占用，防止不同进程同时看到剩余名额而超发。锁仅覆盖接收阶段，不跨越模型生成。所有工作进程必须使用同一个 MySQL 数据库和相同额度配置。

额度直接从未删除、活动请求非空且到期时间晚于当前时间的会话统计，不维护第二份计数，因此完成、失败和中断清除占用后自然释放名额，进程退出遗留的到期占用也不阻塞名额。Redis 仍只是可选缓存。该上限限制应用内有效生成占用；网络取消传播及供应商实际停止可能存在短暂延迟，不承诺供应商计费侧的严格实时并发数。

前端按会话保存消息、草稿、错误、历史版本和活动请求，`state.current` 只指向选中会话。后台事件只写所属会话，切回生成中的会话直接展示本地实时正文；后台完成只校准该会话历史。列表标记本页面正在执行的会话，停止按钮只停止当前会话；pagehide 中断本页面全部任务，真正销毁页面时同时停止接收旧事件。其他标签页任务状态同步仍属后续项，本轮未增加。
字段、索引、表中文注释见 `models/chat.py` 和迁移 `24c713a60f20`，不增加物理外键。

Redis 键为 `fengxuan:chat:context:candidates-v2:{user_id}:{session_id}:{history_version}:{轮数}:{字符上限}`；旧格式由 TTL 自然过期。
仅缓存近期成功轮次，不缓存身份授权、完整历史列表或未完成的流片段。查询缓存前已经检查本人归属。
成功写入后更新 MySQL 历史版本，并尽力排队预热下一轮候选；跳过、失败或尚未预热时，下次照常按需回源，旧版本由 TTL 自动回收。
Redis 读写失败记录业务键后回源，不打印正文。缓存结构和角色顺序也会校验，损坏后重新查询。
缓存保存与当前问题无关的候选消息、history_truncated 和 latest_round_omitted。候选仍受轮数和历史字符上限限制；查询额外查看一轮，以识别更早历史被省略，不查询全量历史。命中和回源均校验完整问答、角色顺序、长度及裁剪标记。预算窗口、输出预留或当前问题改变后，重新计算最终计划，不复用旧问题的裁剪结果。

### 完整请求预算与长对话

有效输入预算为 `min(CHAT_INPUT_TOKEN_BUDGET, LLM_CONTEXT_WINDOW_TOKENS - LLM_MAX_OUTPUT_TOKENS - CHAT_CONTEXT_SAFETY_TOKENS)`。默认值是应用侧保守额度，使用者应保证兼容窗口不高于实际供应商模型容量；不是宣称 deepseek-flash 的最大容量仅为 32768，也不自动调用供应商发现接口。

估算器将 UTF-8 字节数作为文本估算量，每条消息另加 16、整个请求另加 32 的结构余量；不新增分词依赖。这通常偏保守，但不是供应商 tokenizer 的精确计数，也不保证所有兼容模型的真实计数都低于该估算。实际 prompt_tokens/completion_tokens 仍只使用供应商返回值，估算日志不会覆盖数据库实际用量字段。

系统提示词和完整当前问题优先保留，再从最近完整成功问答向前选择；一轮放不下就停止，不跳过中间轮次拼接更早记录，不截断代码或消息，不降低配置的输出预留。原有历史轮数及字符上限仍是额外限制。没有历史时不提示；较早轮次被省略时提示保留轮数；最近一轮也放不下时明确提示本次没有携带历史，请用户补充关键信息。完整 MySQL 历史不删改。

若系统提示词和当前问题本身超限，接收阶段返回 `400/5011`，不写入问题、助手占位或生成占用；窗口无法预留输出和安全空间时返回 `503/5012`。模型请求组装前再次校验完整输入；若接收后才发生预算异常，则通过既有安全 SSE 错误收尾。前端将接收前的这两种错误作为明确拒绝，保留草稿，不误进入未知结果查询。

普通裁剪提示存于对应会话的页面内存，不作为失败、不阻止回答、不持久化到消息表；页面刷新后不还原上一次裁剪提示。不同会话互不覆盖，下一次发送清除旧提示。日志仅记录用户/会话/请求、模型、估算量、输入预算、保留轮数和裁剪标记，不记录正文。没有加入历史摘要、长期记忆或向量库。

## 中断和故障

### 完成响应与可选预热解耦

只有最终结果成功保存且仍属于当前请求时才提交预热；失败、中断、保存失败或迟到结果不入队。提交使用 put_nowait，队列满、停用、未启动或提交异常均跳过，不等待空位，不改变已保存消息，不影响 completed 或 SSE EOF。队列由 main 生命周期托管，经 API 依赖传给流式服务；未提供队列的调用仍能正常完成。

每进程默认 2 个工作任务、最多排队 20 个；全站生成并发上限仍由 MySQL 管理，不能用生成上限代替预热上限。预热在同步线程里运行，每个任务只携带会话工厂和复制后的用户/会话/请求/版本快照，清除不需要的问题正文，不携带请求数据库会话。按数据源、用户、会话、下一历史版本去重；任务完成释放去重记账。旧预热只写旧版本键，不能覆盖更晚版本。

预热普通异常记录业务编号和异常类型，不记录错误原文、问题或回答，也不重新调用模型；失败后继续处理下一个任务。日志将模型结束与预热结束分别记录，预热耗时不再计入成功响应的等待路径。

关闭时立即停止接收，丢弃未执行任务，默认最多等待执行中任务 1 秒，再取消并回收异步工作任务。此期限仅限制生命周期等待，不会强行杀死已经运行的同步 SQL/Redis 调用；同步调用继续到自身结束或驱动超时，进程最终退出仍可能受底层 I/O 影响。Redis 当前有连接及读写超时，不能把本配置误解为驱动超时或严格的进程退出时间。

前端把初次读取的 loadingHistory 与事后 syncingHistory 分开。completed 更新消息状态，SSE 结束后释放 sending；后台历史校准不禁用输入或发送。同步失败保留本地完成回答和草稿，只显示“本轮结果已保存，历史暂未同步”及“重试同步历史”；401 仍走既有重新登录流程。主动重试同步只 GET 历史，不新建生成。

新发送、重试资格查询或原结果确认会增加会话历史读取版本，清除旧同步状态；旧 GET 的成功、失败和 finally 都不得覆盖新任务。结果恢复按请求原位置替换，避免多个尚未校准的临时负编号打乱问答顺序；分页用 request_id/role 去重，避免真实编号与临时编号造成重复。明确拒绝接收的临时轮次立即移除，不必等待慢历史查询。

tests/test_chat_prewarm.py 验证预热阻塞时 completed/EOF 已结束且 MySQL 已保存、实际接口队列接入、容量与工作数限制、去重和快照、提交及执行故障、版本交错、关闭期限和生命周期；前端 chat-history-sync.test.mjs 验证立即发送、迟到历史隔离、同步重试、认证失效、临时编号及拒绝占位清理。本轮无新依赖、表结构或迁移，原断线收尾保持不变。

### 同账号标签页通知、校准及只读观察（第九项）

同源、同浏览器存储分区内使用原生 BroadcastChannel。发起页在服务器接受请求、新建会话和生成结束/停止时发送变更提示；只含 kind、user_id、session_id、request_id、source_id，不传播 token、问题或回答。用户编号来自已验证的本人资料；未取得编号时仍监听登录存储和可见性。其他账号、自身回声、超长或带未知字段的通知直接忽略；有效通知合并 150ms 后重新查询本人接口，不信任通知中的任务状态。

页面可见或重新获得焦点、后退缓存恢复时刷新会话列表并校准选中会话。本地 SSE 活动时不覆盖其内容；观察其他页面的任务时每 2 秒读取状态和已保存进度，空闲时每 10 秒刷新列表及状态，补偿通知缺失。单页面仅运行一个校准流程；隐藏时取消观察、停止轮询，本地 SSE 继续。通道不支持、创建受限或发送失败时仍保留轮询和可见校准，读取失败独立提示“同步状态”。

远端进度是 MySQL 已保存快照，存在保存间隔与查询间隔，不是重放其他页面的 SSE。结束后校准完整历史；查询间恰好完成也会校准。账号快照、本地读取版本、服务端历史版本、选中会话与活动请求共同约束回写，迟到查询不能覆盖新本地流，旧 UUID 完成不能解除后来新 UUID 的占用。

任务归属只由本页真实发送产生。观察、通知、历史加载或重试资格查询都不能授予停止权限；观察页无停止按钮，关闭观察页不调用中断接口。切换会话不改变归属，离开或关闭页面仍只停止本页发起的活动/未知请求。后端仍按本人权限校验 UUID，并未引入标签页级授权。

storage 中的登录键变化或 clear、可见/焦点校准发现 token 变化时，立即撤销旧页面、清空旧消息和草稿、取消旧 SSE/查询并忽略迟到结果，不用新凭证发送旧中断。身份仓库采用当前存储但不写回、不删除新 token；退出转登录，换号重新加载页面并验证本人和权限。没有恢复发起页控制权或跨设备即时推送。

实现定位：前端 chatTabSync.ts 托管通知、监听器和定时器；chatRemote.ts 负责服务器校准和只读观察；utils/tabChannel.ts 只封装浏览器通道。默认间隔在 chatTabSync.ts 顶部常量；状态查询位于后端 chat_history.get_session_state，接口只转调服务。两端部署需一起更新，无新依赖、配置项、表或迁移。

### 原请求确认与最近失败问题重试

网络断开、未收到终结事件、重复编号 `409/3003`，或 SSE 返回保存失败 `5000` 时，前端查询原 UUID，不自动创建新生成。已保存 completed 时恢复正文并清除本次错误和对应重发草稿；failed/interrupted 时展示已保存正文并允许手动重试候选。generating 或暂未找到时最多查询 4 次，查询间隔 1 秒、单次 HTTP 超时 10 秒；查询服务故障立即保留未知状态，不持续轮询。

确认中或未知结果期间，该会话禁止发送和重试，页面保留“再次查询结果”入口，其他会话仍可发送。404 仅说明当前没有查到已提交记录，不能证明原请求未送达。只有本站明确在创建轮次前返回的拒绝业务码（例如满额 3004、同会话忙 3002、未配置 Key 5002）才恢复问题供用户发送，不仅凭代理 HTTP 4xx 猜测未接收。完整终态通过普通历史分页恢复时仍保留。

最近失败或中断的助手回答显示“重新发送此问题”。点击后先查询当前资格；后端显示已完成则只恢复答案，其他不满足条件时不提交。允许时使用新的 request_id 并附带 retry_of_request_id；服务在原有发送事务中再次检查：原问题属于本人会话、为最近轮次、回答失败或中断、正文一致且当前会话无活动占用。校验失败返回 `409/3007`，新旧请求的全站额度、请求去重和行锁规则仍有效。

重试保留原失败记录和部分正文，作为新的问题/回答轮次调用当前配置模型，失败和中断记录不进入成功上下文；不是从原片段续传。retry_of_request_id 仅用于提交校验，不新增持久化关联字段。本轮没有新表、迁移、依赖或环境配置。

恢复按会话隔离，结果查询不会覆盖另一个会话；关闭或离开页面会取消查询、清理等待定时器，并尽力中断本页发起且结果未知的原任务。仅查看其他标签页的生成记录不会取得其中断控制权。恢复状态只在页面内存中，刷新后仍按 MySQL 已提交历史恢复，不在浏览器持久化请求或聊天正文。

`tests/test_chat_requests.py` 覆盖请求终态、归属、删除、旧轮次定位、到期恢复及重试二次校验；真实 API 测试验证查询—新编号重试—旧记录保留。前端 `chat-recovery.test.mjs` 覆盖丢失完成通知、暂未找到、查询故障、重复编号、连续点击、切换及离开页面、满额和代理超时；测试共享边界在 `chat-support.mjs`，执行实际交互代码。

页面使用 AbortController 关闭字节流，并尽力发送带认证的 keepalive 中断通知。
正文快照默认每 2 秒限频保存到已有 chat_messages.content，仍保持 generating，不释放占用、不延长生成到期时间、不增加历史缓存版本。
每次快照都校验本人归属、活动请求和消息状态；旧请求、已完成记录和较短快照不能覆盖已有正文。失败和中断快照仍不进入成功上下文。
周期保存失败会记录安全日志并按间隔重试，最终完成或中断保存不受周期限制。最近一次成功保存之后的片段仍可能丢失，不保证断电零丢失。
后端在流等待时发送心跳，传输断开或异步取消都会关闭上游请求，并在屏蔽取消的短收尾中保存部分正文。
各流资源独立关闭；普通关闭异常只记录业务编号、资源名和异常类型，不阻断其他资源收尾或结果保存。
取消异常继续传播，但 finally 仍会尝试保存已收到的正文和释放生成占用；数据库本身不可用时仍明确记录保存失败。
显式中断与流收尾可能同时发生，二者均按请求编号幂等处理，不会影响后来发起的请求。
浏览器强制崩溃、断电或网络黑洞不能保证立即通知服务端。总生成时限到期会失败；进程退出遗留占用由后台启动扫描及周期扫描恢复为中断，不必等待用户访问。读取历史和发送前的惰性恢复仍保留。

后台每轮最多恢复配置数量的有效会话；仅处理已有占用且到期时间已过的记录，缺失到期时间沿用原有规则视为损坏占用并恢复。会话软删除时跳过。MySQL 在同一短事务内用 FOR UPDATE SKIP LOCKED 获取记录，避免多个工作进程争抢已锁定会话；锁内再次核对当前到期时间，再按当前 request_id 更新生成消息。已保存正文保留，状态改为 interrupted、错误码 generation_expired，清除占用并增加历史版本；迟到结果不能清除新请求。

数据库故障时整批回滚，只记录异常类型并在下一轮重试，不阻止 HTTP 服务启动。关闭服务时唤醒等待并等待当前数据库批次完成，不遗留后台任务。默认每批结束后等待 30 秒，存在积压或数据库故障时可能需要多轮；恢复不是即时保证，也不会重新生成丢失正文。不需要 Redis 分布式锁、新依赖或数据库迁移。
中断和失败轮次不发送给下一轮模型，不把不完整回答当成成功历史。

模型连接截断、无结束标记、无正文、格式错误、超长输出和未知结束原因均视为失败。
模型错误从客户端到 SSE 和历史记录保留分类，使用下表的固定安全文案，不读取或透传上游错误正文。HTTP 状态只表示上游大类，不根据供应商私有错误文案猜测更细原因。

| 上游情况 | 历史 error_code | SSE code | 页面提示方向 |
|---|---|---|---|
| HTTP 401 | model_authentication_failed | 5003 | 联系管理员检查 API Key |
| HTTP 403 | model_permission_denied | 5004 | 联系管理员检查模型权限 |
| HTTP 429 | model_rate_limited | 5005 | 稍后重新发送 |
| HTTP 402 | model_payment_required | 5006 | 联系管理员检查充值或计费状态 |
| 其他 HTTP 4xx（含 400、404、422） | model_request_rejected | 5007 | 联系管理员检查模型名称和请求配置 |
| HTTP 408/504、HTTPX 超时、生成总时限到期 | model_timeout | 5008 | 响应超时，重新发送 |
| 其他 HTTPX 传输故障 | model_connection_failed | 5009 | 连接异常，稍后重新发送 |
| 无效 JSON/片段、流内错误、缺结束标记或普通文本结束条件不满足 | model_protocol_error | 5010 | 返回格式异常或连接提前结束 |
| 其他上游 HTTP 错误及未细分模型错误 | model_unavailable | 5001 | 模型暂不可用 |
| 系统提示词与当前问题超过有效输入预算 | model_context_budget_exceeded | 5011 | 缩短问题或联系管理员调整配置 |
| 预算窗口无法预留输出及安全空间 | model_budget_configuration_invalid | 5012 | 联系管理员调整配置 |

连接重试策略保持不变：只在尚未开始读取片段时，按 LLM_MAX_RETRIES 有限重试传输故障及 HTTP 429/502/503/504；默认 0。最后一次失败保留具体分类。401/402/403/400 等不会重试；收到任何片段后发生故障不会重播请求。HTTP 200 内的 error 帧归为协议异常，不解析供应商私有错误结构。

已开始的 SSE HTTP 响应仍为 200，失败由末尾 error 事件的 code/message 表达；上游 401 不会转换成本站登录失效。前端现有逻辑展示该安全文案；历史接口返回 error_code，刷新后消息仍显示失败，尚未增加每条历史消息的分类文案展示。历史中的旧 model_unavailable 记录保留原值，不回填猜测原因。

结果保存失败时返回“回答未能保存”，不会发送 completed。达到输出长度上限时保留 length 标记供页面提示。
日志只保存用户/会话/请求编号、模型、长度、耗时、用量、状态和错误标识，不打印原始提示词、回答、上游错误正文或 Key。
前端不再按纯文本展示模型输出，而是用本地白名单渲染器 `src/utils/markdown.ts` 把回答转成 HTML：先转义原文，再只生成 p、h1~h3、ul/ol/li、strong、em、code、pre、blockquote、hr、a 这些固定标签，链接仅允许 http/https，不引入第三方渲染库。系统提示词同步放开轻量 Markdown（标题最多三级、列表不嵌套、禁用表格/图片/脚注/数学公式/HTML）。模型输出里的尖括号因此只会显示成文字，不会被执行。

## 文件定位

| 文件 | 职责 |
|---|---|
| app/core/config.py、chat.py、chat_errors.py | 配置、固定类型和安全错误 |
| app/models/chat.py、models/__init__.py | 两张表及模型登记 |
| alembic/versions/24c713a60f20_create_chat_tables.py | 新增表和索引的迁移 |
| app/schemas/chat.py | 请求、历史、上下文和生成结果结构 |
| app/services/chat_history.py | 本人归属和分页历史 |
| app/services/chat_recovery.py | 有界过期恢复事务和随应用启停的后台任务 |
| app/services/chat_turns.py | 接收、去重、结束、中断和过期恢复 |
| app/services/chat_capacity.py | 全站准入行锁、有效占用统计和满额拒绝 |
| app/services/chat_requests.py | 请求结果定位及最近失败问题的重试资格校验 |
| app/services/chat_prewarm.py | 非阻塞提交、受控预热任务及随应用启停的队列 |
| app/llm/tokens.py、app/services/chat_budget.py | 完整输入保守估算、窗口预留及完整轮次选择 |
| alembic/versions/7e9204b6d815_add_chat_generation_gate.py | 新增准入锁表、固定记录和占用索引 |
| app/services/chat_context.py | 有界上下文、版本缓存和预热 |
| app/services/chat_stream.py | 流式生命周期和落库收尾 |
| app/llm/client.py、llm/prompts/chat.txt | 唯一模型调用入口及系统提示词 |
| app/utils/cache.py、utils/sse.py | Redis 封装和统一 SSE 编码、响应关闭 |
| app/api/v1/chat.py、main.py | 路由和装配 |
| pyproject.toml、uv.lock | 已批准的 redis 与 httpx 运行依赖 |
| 前端 src/types/chat.ts、api/chat.ts | 接口类型和普通/流式请求 |
| 前端 src/utils/chatStream.ts | 跨字节、跨网络包 SSE 解码 |
| 前端 src/composables/useChat.ts | 页面局部交互、过期响应隔离和中断 |
| 前端 src/composables/chatRecovery.ts | 有限确认原结果、未知状态保护及手动重试前的资格查询 |
| 前端 src/composables/chatHistory.ts | 初次加载与非阻塞校准、独立同步提示及读取版本隔离 |
| 前端 src/composables/chatTabSync.ts、chatRemote.ts、utils/tabChannel.ts | 标签页提示、可见校准、只读观察、登录变化清理与归属保护 |
| 前端 src/stores/auth.ts、utils/token.ts、views/ChatView.vue | 采用外部登录状态、共享存储键与观察提示，不擦除新凭证 |
| tests/test_chat_api.py、test_chat_requests.py、test_chat_live.py | 状态权限、过期恢复、新旧 UUID、禁止缓存及真实 HTTP 观察/断线 |
| 前端 tests/chat-tabs.test.mjs、chat-auth-sync.test.mjs、tab-browser.mjs、chat-support.mjs | 多窗口、时钟和通道隔离、晚到响应、身份更换与严格归属回归 |
| 前端 src/components/ChatSessionList.vue | 纯展示会话列表和本页面生成状态，避免视图超过 400 行 |
| 前端 src/views/ChatView.vue、HomeView.vue、router/index.ts | 对话页面、首页入口和路由 |
| tests/chat_support.py、isolated_settings.py | 内存测试支持和禁用配置文件的 pytest 插件 |
| tests/test_chat_partial_save.py、partial_save_worker.py | 保存限频、旧请求隔离和专用子进程异常退出恢复测试 |
| tests/test_chat_recovery.py | 自动恢复、批量限制、故障重试回滚、旧请求隔离和启停测试 |
| tests/test_model_errors.py | HTTP/传输/协议错误分类、重试边界、SSE 与历史一致性和脱敏测试 |
| tests/test_chat_capacity.py | 上限、跨用户共享、释放、过期排除和准入记录失效回归 |
| tests/test_chat_requests.py | 终态、归属、历史定位及原子重试校验 |
| tests/test_chat_budget.py、前端 tests/chat-context.test.mjs | 预算边界、缓存动态重算、历史保留及普通提示隔离 |
| tests/test_chat_prewarm.py、前端 tests/chat-history-sync.test.mjs | 预热与完成响应解耦、有界执行、生命周期及立即追问竞争 |
| tests/test_chat_*.py、test_llm_client.py、mysql_chat_checks.py | 接口、状态、缓存、真实断线和独立 MySQL 验证 |
| 前端 tests/chat-*.test.mjs、package.json | 无新依赖的流解码和真实交互模块测试 |
| 两端 docs/architecture.md、progress.md、commands.md | 架构、进度和命令同步 |

修改接口地址、超时、轮数等参数时看 `core/config.py`；修改系统提示词时看 `llm/prompts/chat.txt`。
前端流总时限在 `src/api/chat.ts`，默认 90 秒。若后端调大生成时限，也要对应调整。

## 验证与限制

常规回归命令为 `uv run --no-sync python -m pytest -p tests.isolated_settings -q`。
插件在收集测试前提供虚拟配置，不读取 `.env`，常规数据库用例使用独立内存库。
`test_chat_live.py` 会启动随机本地端口，使用真实 HTTP 断开验证中断持久化，然后关闭测试服务。
前端运行 `npm run test:chat` 和 `npm run build`。测试执行真实解码、交互模块，只替换网络和身份边界。
`mysql_chat_checks.py` 只用于显式指定的独立 `chat_verify` 测试库，不清表，不允许真实库凭证。
本次独立 MySQL 验证包括两条真实对话迁移、模型一致性、三张表的 SHOW FULL COLUMNS/SHOW CREATE TABLE、同会话竞争，以及两个连接池的八请求竞争；剩余三名额时恰好放行三请求。临时隔离容器验证后已关闭并自动清理。
尚未用用户真实 Key 调用 DeepSeek，也未代替用户完成生产反向代理的流式验收。

最终验证结果：第九项后端常规回归 172 passed，前端对话 56 tests passed、布局 3 passed；类型检查、构建、compileall、修改文件的可读性检查通过。新增状态接口及三处前端边界问题、禁止缓存均先复现再修复。此前独立 MySQL 3 passed 和配置模板验证记录保留，本轮无表结构或配置变更，不重复操作真实或隔离 MySQL。
本轮真实 HTTP 输出 Application startup complete、状态和结果 GET 200，观察期间仍为 generating，断线后保存中断并正常关闭。临时 Vite ready，/chat 和 ChatView 模块均 200，验证后已停止；未进行真实模型或真实账号多标签页浏览器验收。
新增四条永久回归覆盖上游两类关闭异常、片段流关闭失败后继续关闭模型连接，以及取消异常仍保存部分回答；其中三条先在旧实现复现失败。
第二项另增十条永久回归，覆盖未结束时落库、限频、无新增跳过、失败重试、等待期间保存、请求隔离及最终完整保存；独立子进程保存后直接退出并跳过 finally，主进程仍能恢复正文。
真实本机 HTTP 测试输出 Application startup complete，并在客户端断开后验证部分回答和中断状态；测试服务已正常关闭。
后端保留一项既有 TestClient 弃用警告，前端构建保留体积提示；本次未为消除提示引入额外依赖或重构。
新增的是永久测试和文档，没有临时验证脚本；独立 MySQL 测试容器在验证完成后停止并清理。



- 日期：2026-11-25。
- 决定：其他标签页不实时移除被删会话，删除不新增广播；切回或刷新时由既有的可见性校准刷新列表。
- 说明：`chatTabSync` 的通知字段有白名单和对应测试，为删除单独扩一种消息会动 schema 与断言；第一版先接受「切回即消失」，等有实际需要再扩。
