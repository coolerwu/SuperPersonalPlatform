# CLAUDE.md

本文件是新项目 `Agent Command Center` 的协作约定。默认使用中文回复用户。

## 需求与方案

- `docs/intent.md` 是需求唯一来源；没有它覆盖的改动不进入实现。
- 方案先定后做；涉及新依赖、破坏性接口、数据迁移或 schema 变更时，动手前先列出并等确认。
- 每次修改代码后，按用户要求做 3 次自我 code review，并如实标注发现。

## 当前架构

- v1 从 0 到 1 重建，旧 `server/`、`web/` 与旧数据模型已删除，不做兼容。
- `apps/api`：FastAPI + deepagents + LangGraph。
- `apps/web`：React + Vite + TypeScript。
- `deploy/`：systemd unit 与 `.env` 模板（生产）。
- 执行核：`AgentExecutor` 端口 + `DeepAgentExecutor`；平台拥有 session/messages/run，deepagents checkpointer 只存 Agent 状态。
- Provider 模型调用在模型类上重试（`apps/api/app/services/model_retry.py` 的 `ModelRetryMixin`）：429/5xx/超时/连接错误最多重试 3 次（指数退避 + jitter），按 `ModelError.is_retryable` 判定，4xx 不重试；`ChatOpenAI` 的 SDK 自身重试关闭，避免叠加。重试挂在模型实例上，因此主 Agent、general-purpose 子 Agent、summarization 和 step-limit 总结调用都会覆盖。流式调用只有在本次尝试尚未输出任何 token 时才重试；已输出片段后失败直接报错，避免前端把两次尝试的文本拼起来。可重试失败记录 warning 日志（agent/session/异常类型）。
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`，没有 nginx/Postgres 容器。
- v1 做 Chat、多 Agent、默认 general-purpose Subagent、Checkpoint/Runs、集中配置页、长期记忆和多渠道（首发微信个人号）。
- v1 暴露工作区内的 filesystem 工具（ls / read_file / write_file / edit_file / delete / glob / grep，由 `FilesystemMiddleware` 提供）；宿主机 shell（`execute`，`LocalShellBackend`）与代码解释器（`eval`，`CodeInterpreterMiddleware`）始终对所有 Agent 开放，不需要技能开关或读取授权；backend 固定为 `LocalShellBackend`，每次 Run 都挂载解释器中间件。浏览器工具通过 `web-browser` 技能门控解锁（Playwright 无头 Chromium，默认文本快照，最多 2 个标签页，可按需返回截图）。memory 由平台管理和注入，不允许 Agent 自主修改 instructions。
- Skills/Tools：全局技能库 `~/.super-personal-platform/skills/<name>/SKILL.md`（`APP_SKILLS_DIR` 可覆盖）+ 每个 Agent 的 `enabled_skills` 启用列表。系统提示只列出启用技能；模型用 `read_file` 读取 `/skills/<name>/SKILL.md` 后，技能在 `metadata.include_tools` 中声明的已注册工具才解锁，读取前不可见也不可调用；文件工具始终可用。`execute` / `eval` 不来自技能注册表：执行核的 backend 始终是 `LocalShellBackend`（宿主机 shell，无沙箱，子进程只注入 PATH/HOME/LANG），每次 Run 都挂载 `CodeInterpreterMiddleware`（QuickJS，无文件与网络，PTC 只开放文件工具，`subagents=True`）；两者对所有 Agent 始终可用，不受启用列表影响。上下文被摘要压缩后，启用且声明了 `include_tools` 的技能会以 `pinned_skill` 消息自动重新注入正文，技能内容与工具解锁不会因压缩丢失。技能工具来自代码注册表 `apps/api/app/services/skill_tools.py`（`web_search` / `web_fetch`，DeepSeek Search/Fetch API，按成功请求计费）、`apps/api/app/services/browser_tools.py`（`browser_open` / `browser_snapshot` / `browser_click` / `browser_fill` / `browser_press` / `browser_screenshot` / `browser_close`）、`apps/api/app/services/memory_tools.py`（`memory_list` / `memory_update` / `memory_delete`，按 `user` + 当前 Agent scope 授权）和 `apps/api/app/services/backtest_tools.py`（`backtest_run` / `backtest_status` / `backtest_result` / `backtest_cancel` / `market_probe` / `market_cache_status`，由 `quant-backtest` 技能门控，创建的回测与网页共用 `BacktestWorker` 和 `market.db`）、`apps/api/app/services/cron_tools.py`（`cron_list` / `cron_targets` / `cron_create` / `cron_update` / `cron_delete` / `cron_run`，由 `cron-scheduler` 技能门控，只操作当前 Agent 的任务，与网页共用 `cron_service` 和 `CronRuntime`），不在技能目录执行 Python。Agent 可以在对话中直接用文件工具创建或修改 `/skills/**` 下的技能，不需要表单；控制点是当前 Agent 启用了哪些技能，启用技能即允许它声明的已注册工具；技能声明本身不是安全边界。首次启动 seed `web-search`、`web-fetch`、`web-browser`、`memory-organizer`、`quant-backtest`、`cron-scheduler` 和 `knowledge-base` 技能（`web-search` 同时声明 `web_search` 和 `web_fetch`；`web-fetch` 保留用于只抓取网页的场景；`quant-backtest` 声明六个回测/行情工具，不自动启用；`cron-scheduler` 声明六个定时任务工具，不自动启用；`knowledge-base` 不声明工具，用于把知识库镜像挂到 `/knowledge/`，不自动启用）；内置技能的写入哈希记录在 `~/.super-personal-platform/skills/.seed_state.json`，启动时只有内容与记录一致（用户未修改）的内置技能才会随版本更新，用户改过的保持不动；无记录的文件不动，直到其内容与内置默认一致后开始跟踪。「技能」页面可以打开现有 SKILL.md 查看并直接编辑保存（原子写入，保存前校验 frontmatter）；新建技能仍通过对话完成。AI 可以在对话中用平台工具 `skill_save` 主动创建技能（通过 `skill_save` 每次 Run 最多 3 个，写入 `metadata.source: ai`，不覆盖已有技能）；技能来源分 `builtin`（seed 跟踪）/ `ai` / `manual`，技能页按「系统技能 / 交互创建」分组，非系统技能可删除（同时从所有 Agent 的 `enabled_skills` 移除）。AI 创建的技能会自动加入当前 Agent 的 `enabled_skills`，同一会话的下一轮 Run 生效（当前 Run 不加载，不跨 Agent 启用）；手动创建的技能仍需用户启用，读取 SKILL.md 后工具才解锁。`skill_save` 是始终可用的平台工具，不参与技能门控，「工具」页单列平台工具。
- Browser：每次 Run 懒启动一个 Chromium + context，`BROWSER_CONCURRENCY`（默认 5）限制标签页；执行器在 `finally` 关闭（含失败、超时、取消）；只允许公开 http/https，拒绝 localhost、私网、`file://` 和下载；默认返回文字和元素 ref，只有用户明确要求时才用 `browser_screenshot` 返回 PNG 图片（需要 Provider 支持图片输入）；搜索优先尝试移动/轻量入口，遇到验证码或 HTTP 403/429 返回明确错误并提示换入口，不尝试绕过；相关配置为 `BROWSER_*`。
- Browser 截图：`browser_screenshot` 返回给模型的 PNG 同时保存到 `workspaces/<agent_id>/screenshots/<session_id>/<run_id>/`，前端在对应 assistant 消息下方展示缩略图，Agent 文件工具可访问；删除会话或彻底删除 Agent 时清理对应目录。
- 知识库：`apps/api/app/services/knowledge_sync.py` 在 API 进程内把配置页「知识库」tab 保存的 WebDAV 目录双向同步到 `~/.super-personal-platform/knowledge/`（`APP_KNOWLEDGE_DIR` 可覆盖），启动后立即同步一次，之后按配置的间隔（默认 300 秒）增量同步；状态存 `data/knowledge_sync_state.json`（etag + 本地哈希），只传变化文件。配置存 `knowledge_configs` 单行表（启用开关、地址、账号、密码、根目录、间隔），`GET/PUT /api/knowledge` 读写、`POST /api/knowledge/sync` 立即触发；密码只进 `app.db`，接口只返回 `has_password`，不写日志。`KB_WEBDAV_*` 只在数据库尚无配置时作为首次启动的种子值。远端和本地都改过时远端保留原名，本地另存 `<name>.conflict-local-<时间戳>` 后上传；远端删除且本地未修改时删本地，本地删除不传播（下轮恢复）；路径限制在 root 内、拒绝 `..` 和符号链接，单文件上限 `KB_MAX_FILE_BYTES`（默认 50 MB）。坚果云限流（免费账号约 600 次/30 分钟、所有第三方应用共享；单次 PROPFIND 最多 750 项；不支持 Depth infinity）：客户端按 `KB_REQUEST_INTERVAL_SECONDS`（默认 4.5 秒）限速，默认跳过 `KB_EXCLUDE_DIRS` 中的 `.obsidian`、`.git` 等目录，收到 `BlockedTemporarily` 或 HTTP 503 时暂停 `KB_BLOCKED_RETRY_SECONDS`（默认 1800 秒）；列表达到 `KB_MAX_LISTING_ENTRIES`、被截断或子目录读取失败时本轮跳过本地删除判断。状态文件记录远端身份哈希（URL + 账号 + 根目录）；地址、账号或根目录变化时把本地镜像和状态移到 `.old-<时间戳>` 备份后重建，避免旧文件被上传到新根目录。执行器只在 Agent 启用 `knowledge-base` 时给 backend 增加 `/knowledge/` 路由（`FilesystemBackend`），Agent 用现有文件工具读写；未启用不挂载。`GET /api/system` 的 `knowledge` 字段返回同步状态（不返回 URL、账号或密码）。
- 记忆：`memory_items`（scope=user|agent，无 status）+ `run_memories`；user memory 全局、agent memory 按 Agent 隔离；session 工作记忆仍由 checkpoint 负责。
- 会话检索：SQLite `messages_fts`（trigram、`content='messages'`、`content_rowid='rowid'`，`messages_ai` / `messages_ad` / `messages_au` 触发器；首次创建或空索引时 `rebuild` 回填）索引历史消息；平台工具 `session_search` / `session_read` 始终可用，按当前 Agent 隔离，返回命中片段与前后文；侧栏搜索可定位到具体消息。FTS 失败时降级 `LIKE`。
- 写入：配置页手动 CRUD、对话里“记住……”写 user memory、每个 Agent 的 `auto_memory` 开关在 Run 完成后提取 agent memory；先用 FTS5（trigram）找相关历史记忆，再让模型返回 add/update/delete 做合并，最多 12 个操作、200 条上限。提取在 Run 落 completed 后作为后台任务执行，不占用 Run 并发槽；同一 Agent 的提取在进程内串行（单进程部署），后一次能看到前一次刚写入的记忆，避免并发写入语义重复的条目。
- 读取：每次 Run 按关键词/标签/最近使用取 Top N（最多 12 条、约 1500 字）注入，标注为数据而非指令；记录本次使用的 memory id。
- 整理记忆：聊天输入区「＋」菜单的「整理记忆」指令直接发起一个 Run，读取 `memory-organizer` 技能后用 `memory_list` / `memory_update` / `memory_delete` 合并重复、删除失效项，只更新/删除、不新增事实；范围限 user memory 与当前 Agent memory。首次点击会为当前 Agent 自动启用该技能。原记忆页面的「整理记忆」按钮和 `POST /api/memories/consolidate` 已移除。
- Workspace：每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/`（`APP_WORKSPACES_DIR` 可覆盖），不支持自定义路径；配置页只读展示机器目录，并跟随左上角选中的 Agent。执行器用 `FilesystemBackend(virtual_mode=True)` + 默认 `FilesystemMiddleware` 开放工作区文件工具。
- Channels：每个 Agent 可绑定多个渠道，当前只有 `wechat`。个人微信通过腾讯 iLink Bot API 接入（扫码登录、`getupdates` 长轮询、`sendmessage` + 每条回复带回联系人最新的 `context_token`），`WeixinRuntime` 在 API 进程内为每个 active 渠道跑一个轮询任务；不需要公网回调、Webhook、Node 或第三方 puppet。每个外部 1:1 联系人映射一个独立 Session（标题 `微信 · <联系人>`），网页端可见并继续；群聊忽略。入站文本、文本引用、图片和文件都会处理：图片/文件从 iLink CDN 下载，按 AES-128-ECB 解密后保存到 `workspaces/<agent_id>/uploads/wx-<hash>-<name>`，再以附件清单写入用户消息；引用正文格式化为引用块；语音/视频不下载、不落盘，纯语音/视频直接回固定提示且不建 Run，混合消息只处理文字/图片/文件。同一联系人的连续消息在内存中按 `CHANNEL_INBOUND_BATCH_SECONDS`（默认 5 秒，最长 15 秒）打包成一个 Run；打包窗口中不保存 `sync_buf`，flush 成功且无 pending/inflight 后才写入，重启时由 iLink 重放并由 `channel_inbounds.external_message_id` 幂等吸收。微信入站文本整条为斜杠命令时（`/new`、`/clear`、`/help`、`/status`、`/stop`，不区分大小写）在幂等记录后、创建 Run 前拦截：`/new` / `/clear` 先把待打包消息 flush 到旧会话再新建 Session 并重绑 `channel_conversations.session_id`（旧会话保留），`/status` 返回 Agent/会话/任务数，`/stop` 丢弃待打包消息并取消当前会话 queued/running Run，`/help` 与未知命令返回命令说明；命令不创建 Run/Message、不做 5 分钟文本去重，确认消息由运行时直接 `sendmessage` 发送（带 `context_token`，失败只记日志、不补发）。含空格或不符合命令形态的文本照常进入 Agent。入站按 `external_message_id` 幂等；同一 Session 允许排队多个 queued Run，worker 按 Session 串行执行；不做忙碌打断/abort 策略。Run 终态时在同一事务写入 `channel_deliveries`，worker 立即发送最终答复（按 1800 字在段落/代码围栏边界切块），没有发送轮询循环；进程崩溃后启动时补发 pending/attempting（attempting 加「可能重复」标记）。`bot_token` 只存 `app.db`，不返回前端、不写日志。表：`channels` / `channel_conversations` / `channel_inbounds` / `channel_run_links` / `channel_deliveries`。删除渠道保留 Session/Message/Run；删除 Session 解绑 conversation；永久删除 Agent 级联渠道。
- Cron：侧栏「Agent 能力」组的「定时」页面按当前 Agent 管理定时任务。`cron_jobs` 表保存名称、prompt、5 字段 cron 表达式、时区、启用状态、`next_run_at`（UTC）、最近运行和跳过记录、可选推送目标 `push_channel_id` / `push_conversation_id`；一个任务创建时同时创建一条专属 Session（标题 `定时 · <任务名>`，`sessions.origin='cron'`），此后每次触发都在这条 Session 里创建 user Message + queued Run（`source='cron'`），用户在同一会话里的追问进入同一 checkpoint，成为后续触发的上下文；新建任务才新建会话。`CronRuntime` 在 API 进程内每 `CRON_TICK_SECONDS`（默认 20 秒）扫描到期任务，触发时复用 `RunWorker` 与现有并发上限，不新增执行器；调度器依赖单进程部署（多 worker 会重复触发）。同一任务会话已有 queued/running Run 时跳过本次触发并写 `audit_log`；服务停机后每个任务最多补跑一次；Agent 归档或暂停时不触发，恢复后从下一个匹配时间继续。暂停/删除任务不取消已产生的 Run；删除任务保留会话；删除会话后任务在下次触发时自动重建会话。时区按任务的 IANA 时区解释（默认 `CRON_DEFAULT_TIMEZONE=Asia/Shanghai`）。任务可显式配置推送目标：必须是当前 Agent 的 `wechat` 渠道下已有 1:1 联系人会话（`GET /api/channels/{id}/conversations` 列出会话，不返回 `context_token`）；触发时在创建 Run 的同一事务写入 `ChannelRunLink`，结果复用 `ChannelDelivery` / `WeixinRuntime` 分块、重试和补发，未配置则不推送；渠道或联系人删除时清空任务目标，删除定时会话时清理其 Run 的 link/delivery。定时 Run 与普通 Run 权限相同（含 `execute` / `eval`），没有额外审批。内置 `cron-scheduler` 技能（不自动启用）声明 `cron_list` / `cron_targets` / `cron_create` / `cron_update` / `cron_delete` / `cron_run`；Agent 启用技能并读取 SKILL.md 后解锁，只能操作当前 Agent 的任务，创建任务同样新建专属会话、prompt 需要自包含。依赖 `croniter`。
- Backtest：侧边栏「回测」是独立页面，不绑定 Agent，首版只做加密货币现货、单标的、历史回测。行情来自 Bitget 公共 REST（`BITGET_BASE_URL`），按需抓取到 `data/market.db`（`market_candles` / `market_sync_state`，主键 `(symbol, granularity, open_time_ms)`）并做首尾增量更新；每次回测前会扫描区间内中间缺口并尝试从 Bitget 补齐（`MARKET_GAP_REPAIR_BARS` / `MARKET_GAP_REPAIR_RANGES` 限制预算），补不齐的缺口在回测网格中保留为 NaN 行，缺口期间强制空仓、跨缺口收益不计入，结果里明确告警。周期 `1min/3min/5min/15min/30min/1h/4h/6h/12h/1day/1week/1M`，日/周/月请求 Bitget 的 `1Dutc/1Wutc/1Mutc` 按 UTC 对齐，回测只使用完整 K 线。统一出站代理使用标准 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`（`settings` 会把它们导出到进程环境，LLM/搜索/微信/行情共用同一个代理）。策略为页面内 Python `def strategy(df) -> pd.Series`（UTC DatetimeIndex，列 open/high/low/close/volume，返回 [-1,1] 目标仓位），写入临时文件后在独立子进程中执行（超时/内存/进程组隔离，不是安全沙箱）。`warmup_bars`（API 字段，默认 200，0–5000）会在评估区间之前额外加载 K 线；策略在包含预热的完整数据上运行，可以为评估区间建立初始仓位，指标/曲线只从用户选择的开始时间计算。策略还会在截断前缀上重跑并比较重叠区间的信号来检测未来函数，不一致时结果带「疑似使用未来数据」告警（`BACKTEST_FUTURE_CHECK` 默认开启）。`BacktestWorker` 在进程内串行执行（`BACKTEST_CONCURRENCY` 默认 1），状态 `queued/fetching/running/completed/failed/cancelled`，启动时把未完成任务重新排队；结果写入 `backtests` 表（指标 + 下采样净值/回撤/价格 + 交易明细，曲线 ≤2000 点、交易 ≤5000 笔）。接口：`POST/GET /api/backtests`、`GET /api/backtests/meta`、`GET /api/backtests/{id}`、`GET /api/backtests/{id}/result`、`POST /api/backtests/{id}/cancel`、`GET /api/market/candles/status`、`GET /api/market/probe`。Agent 通过 `quant-backtest` 技能解锁 `backtest_tools.py` 的六个工具；`backtest_run` 创建后最多等待 `BACKTEST_TOOL_WAIT_SECONDS`（默认 90 秒），完成返回指标摘要，未完成返回任务 id，之后用 `backtest_status` 查询。后端依赖新增 pandas/numpy，前端图表使用 lightweight-charts。
- 运行时目录统一在 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：`.env`、`data/app.db`、`data/market.db`、`data/checkpoints/`、`data/backtests/`、`data/knowledge_sync_state.json`、`workspaces/`、`skills/`、`knowledge/`。
- 默认 Assistant 使用中文个人助手提示词：先说明自己是个人助手，并列出可做的对话、工作区文件、general-purpose 子 Agent、记忆注入、会话 checkpoint 和技能（读取 SKILL.md 后解锁技能工具）；行为规范参考 Hermes（直接、长度匹配问题、无 filler、反谄媚、深度按需），默认简体中文回复，不宣称 Hermes 身份。新建 Agent 未填写 instructions 时使用同一套默认提示词；运行时不使用任何兜底提示词或兜底 Agent 名称。
- UI 视觉方向：全站采用珍珠冷光画布 + 极光弥散 + 悬浮玻璃（登录页、Chat、配置/记忆/技能/工具/渠道/定时/回测页），主色为浅紫、运行态使用青绿到紫的渐变；Chat 的 assistant 消息为整列玻璃卡片、user 消息为右对齐深色气泡。assistant 消息按 Markdown 渲染；依赖 `react-markdown`、`remark-gfm`、`rehype-highlight`。Chat 顶部有轻量栏（Agent/会话标题 + 详情开关）；≤860px 使用顶部栏 + 汉堡菜单，侧栏变为抽屉、详情变为全屏面板，输入区跟随 `visualViewport` 与安全区收缩。
- 输入区：左侧「＋」菜单提供「上传附件」「整理记忆」和「清空输入」。「整理记忆」见上一节。附件保存到 `workspaces/<agent_id>/uploads/`，单文件上限 10 MB，上传时显示单个文件的进度与大小；图片支持选择、粘贴和拖拽，大图在浏览器端缩到最长边 1600 px 后上传，输入区和消息气泡显示缩略图，点击可看原图。发送时把文件名、工作区路径和字节数写入用户消息，Agent 用文件工具读取，附件不写入数据库；图片还会作为 `image_url` content block 加入当前 Run 的模型输入（最多 4 张、单张 4 MB）。上传文件通过鉴权接口 `GET /api/agents/{agent_id}/uploads/{filename}` 读取。前端把附件块渲染成文件 chip 或图片缩略图、把「整理记忆」渲染成命令卡片（原始指令可展开），消息文本本身保持纯文本，不新增 Message 字段。发送时强制滚到底部；Run 输出期间上滑离开底部会显示浮动的「回到最新」按钮。Run 运行中输入区仍可发送，停止键和发送键并排显示，发送的追问进入同会话队列并显示「已排队，前面的任务完成后会自动执行」。草稿、附件和引用按 session 隔离（key 为 `agent_id + session_id`，未创建会话时用 `__new__`）：切换会话恢复各自内容，发送成功后清除引用，发送失败写回发起会话，「整理记忆」不消费草稿/附件/引用，创建新会话时把草稿和附件带到新会话。
- 消息操作：复制、引用、失败/已停止 Run 重试；消息的复制/引用按钮位于气泡下方、气泡之外，用户消息的时间也随按钮放到气泡外；引用内容显示在输入框上方（可单独取消），发送时以 `> ` 引用块写入消息开头，有附件时排在附件头之后；不做编辑/重新生成/删除单条消息（需要 checkpoint 分支方案）。
- Agent 支持彻底删除 `DELETE /api/agents/{id}/permanent`，级联 sessions/messages/runs/run_events/checkpoints/channels；有活动 Run 时 409。
- 配置页集中管理 Provider（查看/新增/编辑）、Agent 和运行环境；另有独立的「记忆」页面管理 user/agent 记忆。`GET /api/system` 只返回非敏感信息。
- 所有 per-agent 配置都以左上角侧边栏的 Agent 状态为唯一来源；配置页的 Agent tab 列出全部 Agent 摘要（Provider/model、启用技能数、自动记忆、状态），点击行即切换这个全局选中的 Agent，侧边栏同步；下方编辑区只编辑当前 Agent，不持有第二套选择状态。新增 per-agent 配置时必须沿用这一规则。侧栏顶部是「Agent / 量化」两个工作区 tab（默认 Agent）：Agent 工作区按「Agent 工作台 / Agent 能力 / 平台」分组，Agent 工作台放 Agent 选择器、新对话与会话列表，「Agent 能力」组提供「技能」「工具」「渠道」「定时」「记忆」页面，「平台」组放配置与退出；量化工作区只显示回测（不绑定 Agent），不显示 Agent 选择器与会话。技能启停按当前 Agent 立即保存；工具页只展示平台工具、文件工具与技能工具的注册/声明情况，不单独启停工具；渠道页按当前 Agent 管理微信渠道与扫码登录；定时页按当前 Agent 管理定时任务与会话入口。
- Chat 内容列统一宽度：header、消息流、composer 都以 `--chat-lane` 对齐。

## 运行约定

- 每次用户消息、渠道入站消息或手动运行创建一个 Run；状态：`queued / running / completed / failed / cancelled`。渠道 Run 的 `source=wechat`。
- Session 懒创建：点击「新对话」只清空视图，首次发送时才创建 Session 和 Run；不要为了展示空状态提前落库。
- 同一 session 最多一个 running Run，`queued` Run 可以排队；网页 Chat 在已有活动 Run 时不再返回 409，而是创建 queued Run（运行中可继续追问，页面上显示「已排队」，当前 Run 结束后按创建顺序自动执行）；retry 创建时若已有活动 Run 仍返回 409，渠道入站继续排队并按 Session 串行执行。assistant Message 的 `created_at` 使用所属 Run 的创建时间，保证运行中追问的显示顺序是「提问 → 回答 → 追问 → 回答」。不同 session 的 Run 可并发执行，最多 `BROWSER_CONCURRENCY` 个（默认 5）；网页发送不因其他会话的运行中 Run 被禁用，超过并发上限的 Run 保持 `queued`，前端显示「排队中，等待前面的任务完成」。发送/重试的 pending 状态按 session 记录。Run 并发数与浏览器标签页上限绑定，避免多个 Run 叠加出更多 Chromium 实例。
- Run 的 LangGraph 递归上限为 `RUN_RECURSION_LIMIT`（默认 500，主 Agent 与 general-purpose 子 Agent 共用，deepagents 自身默认 9999）。真撞上限时执行器先在同一 checkpoint 上带「继续完成原任务」提示自动续跑一次（`step_limit` 事件 `action=continue`），续跑仍撞上限才用同一模型、带上 checkpoint 里的完整对话（不带工具）生成最终答复（`action=summary`）；Run 仍落 `completed`，输出前标注「已达到执行步骤上限」。
- 服务停止/重启时，正在执行的 Run 重新入队（清空 `started_at`，写入 `reason=service_restart` 的 `run_queued`）并在启动后继续；已请求取消的 Run 落 `cancelled`，不会被重启复活。启动时 `requeue_running_runs` 兜底崩溃留下的 `running`。恢复时执行器把 run_id 作为本轮 `HumanMessage` 的稳定 id 定位 checkpoint：如果本轮输入已写入 checkpoint，就保留中断前的 assistant/tool 消息，追加「继续上次任务」的提示，不再重复原始 input；缺失结果的 tool call 补一条「结果未知、不要重复执行」的 error `ToolMessage`。checkpoint 没有本轮输入时按新 Run 处理。重新入队不是事务回滚：中断前已经发生的文件/浏览器副作用仍然保留，只能依赖提示避免再次执行。
- 前端轮询在服务重启等瞬时连接错误下按指数退避重试（最多约 120 次），不停止 Run；收到 `reason=service_restart` 的 `run_queued` 时清空当前流式文本和思考内容，并显示「服务重启，Run 已重新排队」提示。
- 前端 Run 状态按 session 保存（`activeRun / events / streamText / streamReasoning / activity / notice / tracking`），SSE/轮询订阅也按 session 管理：切走会话保留后台跟踪，切回立即恢复，绝不把 A 的文本渲染到 B；切换会话时先清空旧消息并显示骨架。网页发送现在会排队，不再触发 409；微信入站 5 秒打包后的 Run 也进入同一队列。retry（以及极端竞态）遇到 409 时读取 `detail.active_run_id` 直接接入该 Run（消息未发送、草稿恢复），不显示普通错误。发送成功后用 `GET /api/sessions/{id}/active-run` 判断新 Run 是否已是当前活动 Run：是则接入；否则保留当前 Run 的流式状态并提示已排队，等当前 Run 的 `finishRun` 接入下一条。`finishRun` 拉取终态失败时也要把 `tracking` 置为 false，避免输入区被永久锁住。失败/取消的 Run 用最后一个 `run_queued` 之后的 `run_events` 重建已流出的文本，渲染为「失败」/「已停止」气泡（错误摘要 + 重试），刷新后同样恢复；完成时先加载持久化消息再清空流式文本，并隐藏已被 assistant message 取代的流式气泡。页面可见时前端每 5 秒刷新当前 Agent 的会话列表：渠道入站的新会话、消息数和 active Run 更新侧栏徽标；当前会话出现活动 Run 或消息数增加且没有本地订阅时，自动接上该 Run。会话摘要返回 `channel_type` / `channel_name`，侧栏给微信会话加徽标，输入区提示网页回复不会发回微信。
- Run 事件 append-only，存 `run_events` 表；其中 `assistant_delta` / `reasoning_delta` 是每个流式片段一行，SSE/轮询和刷新恢复依赖它；`tool_call` 记录工具名与解析后的参数，前端据此显示「正在读取 uploads/x.pdf」「正在浏览 example.com」等活动行（超过 10 秒附已用时间，queued 显示「排队中，等待前面的任务完成」）。前端默认折叠原始片段，仅保留 Run/工具/子 Agent/用量等可读事件；`reasoning_delta` 与 `tool_call` 在聊天里按 `run_events` 的 event id 顺序构建可折叠的「执行过程」：连续 `reasoning_delta` 合并为一段思考，`tool_call` 是工具行，工具之后的新思考另起一段；流式期间展开，开始输出答案后自动折叠，完成后默认折叠；刷新后恢复当前/最近一次 Run。子 Agent 的 `reasoning_delta` 目前没有单独 agent 标记，工具行有 agent 标记。流式文本约 80ms 节流后再交给 Markdown 渲染，Markdown 组件 memo 化；重置/重试立即应用新文本。失败 Run 和 API 错误按认证、限流、上下文超限、超时、连接、配置缺失等类别转中文提示，原始错误保留在失败气泡标题和抽屉中。deepagents 的上下文压缩（summarization）属于内部模型调用，其流式片段（`metadata.lc_source=summarization`）必须过滤，不写入 assistant 输出、思考过程或 Run 事件。「执行过程」折叠条在消息内左对齐并占满整行，展开后的思考/工具面板同样使用整行宽度。
- Chat 消息历史按页加载：`GET /api/sessions/{session_id}/messages/page?limit=50&before=<message_id>` 返回 `items / has_more / next_before`；首屏加载最新一页，滚动到距顶部约 80px 时加载更早一页并按 id 去重合并，调整 `scrollTop` 保持位置；运行完成/轮询刷新只合并最新页，不替换已加载的旧页；搜索命中不在窗口内时向前翻页直到出现（最多 20 页，找不到回退全量接口）。旧的全量接口保留作兼容与兜底。
- 生产数据库是 SQLite：`~/.super-personal-platform/data/app.db`；checkpoint 在同目录 `checkpoints/`。时间列用 `UTCDateTime` 保证 SQLite 读回仍是 UTC。
- SQLite 开启 WAL 和 `busy_timeout=5000`，API 与 worker 同进程，不另起服务。
- 每 session 一个 checkpoint thread；刷新/重启后可继续。
- `~/.super-personal-platform/` 是运行时 home；旧平台历史数据已清空，不再作为数据源。删除 Agent 不删除其 workspace 目录。
- 密钥只通过环境变量/secret file 提供，不写入仓库、日志或前端响应。

## 开发命令

```bash
# 安装
python3.12 -m venv .venv
.venv/bin/pip install -e "apps/api[dev]"

# 后端开发
cd apps/api && ../../.venv/bin/uvicorn app.main:app --reload --port 8000

# 前端开发
cd apps/web && npm install && npm run dev
# 测试
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

## 前端构建产物

- `apps/web/dist` 提交到仓库；修改前端后执行 `cd apps/web && npm run build`，把新的 `dist` 一起提交。
- 生产由 FastAPI 直接托管 `apps/web/dist`（SPA fallback 到 `index.html`）；不构建 Web 镜像，也不在生产机安装 Node/npm。

## 提交约定

- 提交前先跑后端测试与前端构建。
- `git status --short` 确认范围；不要提交 `.super-personal-platform/`、`.env`、`data/`。
- 需要提交时执行 `.claude/skills/project-commit/SKILL.md`；该技能需要随新栈更新。
- 只 push `origin`（GitHub）。本地的 `rc-clone` 是独立 checkout，push 不会改动它；需要同步时在 `rc-clone` 里单独 `git pull`，不要从当前仓库对它执行 pull/reset。
- 当前 v1 生产：`qiuqiu@192.168.1.3:~/SuperPersonalPlatform-v1-deploy`，用户级 systemd 服务 `agent-command-center.service`，venv + SQLite，监听 8888。Docker / nginx / Postgres 部署路径已废弃并从仓库移除；旧 unit、旧仓库与旧数据已删除，不能回滚到旧平台。
- 生产从 GitHub `main` pull；`~/.super-personal-platform/.env` 只存在目标机本地，不提交，作为 systemd `EnvironmentFile`。`apps/web/dist` 提交到仓库，由 FastAPI 直接托管。
- 部署流程见 `README.md` 和 `.claude/skills/project-commit/SKILL.md`。

## 文档同步

- 行为、接口、配置、依赖、目录结构变化时同步更新 `docs/intent.md`、本文件和 `README.md`。
- 旧架构文档不再作为依据；需要时从 git 历史查看。
