# Intent

- 状态：现行
- 最后更新：2026-11-11
- 编号水位：F17

本文件是新项目的需求唯一来源。2026-11-11 已按 Claude Agent SDK 迁移后的实现重写；旧的 F1–F90 需求记录（deepagents/LangGraph、记忆系统、rubric、eval、会话全文搜索、Run 历史）随迁移废止，历史文本见 git 历史（cf67560 及之前）。编号从 F1 重新起算。

## 怎么用

- 新需求在末尾追加 `### F<n> <名称>`，编号不复用。
- 需求只写意图、场景、边界和成功标准，不写技术路线。
- 没有本文件覆盖的改动不进入实现。
- 方案先定后做；涉及新依赖、破坏性接口、数据迁移时先确认。

## 需求记录

### F1 单用户自托管 Agent Command Center（现行基线）

- 状态：现行
- 日期：2026-11-11
- 范围：网页 Chat、多 Agent 配置、微信个人号渠道、定时任务、技能库、官方插件集市、知识库镜像、量化回测、交易记录、资产配置、缠论图

#### 背景与目标

单用户、自托管的 Agent 指挥中心。执行核使用 Claude Agent SDK（Python，`ClaudeSDKClient`），模型走 DeepSeek 的 Anthropic 兼容端点。平台只保留调度与业务能力：会话与消息由 SDK transcript 管理，平台配置在 `data/settings.json`，运行时租约在 `running_sessions`。

成功标准：网页、微信、定时三条入口都跑在同一执行核上；重启后正在执行的会话能自动续跑；回测、缠论、资产配置、交易记录、知识库页面行为不变；不依赖 deepagents / LangGraph / langchain-openai。

#### 功能点清单

1. 单用户 token 登录（cookie）。
2. 网页 Chat：流式输出、历史会话、停止、运行中排队追问、Markdown/公式渲染、附件、引用；失败或停止后重新发送即可，不提供单条消息重试。
3. 多 Agent：名称、instructions、Provider、状态；每个 Agent 独立工作区与会话目录；左上角 Agent 选择器是全站 per-agent 配置的唯一来源。
4. 执行核：`ClaudeSDKClient`；会话 UUID 即 SDK `session_id`；transcript 存 `claude/projects/<agent_id>/<uuid>.jsonl`；会话列表/历史/重命名/删除走 SDK。
5. 重启恢复：`running_sessions` 运行时租约 + SDK `resume`；启动时自动续跑，恢复提示要求不重复已完成步骤。
6. 微信个人号渠道：扫码登录、iLink 长轮询、5 秒打包、命令、引用找回、图片/文件、回复投递与补发。
7. 定时任务：5 字段 cron、IANA 时区、专属会话、忙时跳过并审计、可选推送到微信联系人、停机后最多补跑一次。
8. 会话保留清理：按 transcript mtime 与保留天数删除，跳过运行中会话，保留 auto memory。
9. 记忆：用户画像 = `claude/CLAUDE.md`（记忆页单文档编辑）；Agent 指令 = `settings.json`；Agent 笔记由 Claude auto memory 管理。
10. 配置页：Provider、Agent、知识库、运行环境；所有平台配置写入 `data/settings.json`，密钥字段只返回 `has_api_key` / `has_password` / `has_credentials`。
11. 技能库：全局 `claude/skills/` 目录（Claude Code 用户级技能），内置技能 seed 与哈希跟踪；本版技能工具尚未接入 Agent。
12. 知识库：WebDAV 双向镜像到 `knowledge/`，配置与同步状态在设置接口；本版尚未挂载给 Agent。
13. 业务页面：回测、交易记录、资产配置、缠论图，均不绑定 Agent。
14. 前端路由自建（History API），刷新/直达/前进后退可用。

#### 验收标准

- 能创建/切换多个 Agent；每个 Agent 的会话、transcript、工作区隔离。
- Chat 流式输出；刷新后历史从 transcript 读回；同进程刷新可在数秒内通过 `current_turn_id` 重放当前轮缓冲，进程重启或断线才丢失当前输出。
- 执行中可停止；租约删除；重启后正在执行的会话自动续跑。
- 微信、定时入口与网页共用执行核与会话队列。
- 后端测试与前端构建通过。

#### 约束边界

- 单用户、自托管，不做多租户/RBAC。
- 单进程部署；cron 与微信运行时都在 API 进程内，多 worker 会重复触发。
- 不提供进程重启或断线后继续看当前输出、消息时间戳、会话全文搜索、Run 历史、rubric 质检、QuickJS eval、平台级多 Agent 编排或自定义子 Agent 配置；SDK 内置 Task 子 Agent 可用。
- 平台写入的密钥存 `data/settings.json`（权限 0600）；`.env` 提供的密钥仅在首次种子时复制到 settings.json，之后以文件为准，平台不回写 `.env`。密钥不写日志、不返回前端。

#### 非目标

- 平台级多 Agent 编排、自定义子 Agent 配置（SDK 内置 Task 子 Agent 可用）。
- 事件触发、Webhook、主动推送。
- 工具层迁移（技能工具、add_dirs、MCP、浏览器/搜索/回测工具接入 Agent）——下一版本。

### F2 执行核与网页聊天

- 状态：现行
- 日期：2026-11-11
- 来源：Claude Agent SDK 迁移

#### 功能点

1. 每条用户消息创建一个 turn，在 `SessionRegistry` 中按 session 串行执行；不同 session 并发，上限为 `BROWSER_CONCURRENCY`（默认 5）。同一会话在运行中可继续发送，新 turn 排队。
2. `POST /api/chat/messages` 入队并返回 `{session_id, turn_id, accepted}`；`GET /api/sessions/{uuid}/stream?turn_id=...` 以 SSE 转发该轮事件（`assistant_delta`、`reasoning_delta`、`tool_call`、`turn_end`）。
3. `GET /api/sessions/{uuid}/messages?agent_id=...` 一次性返回 transcript 中的用户/assistant 消息；执行过程从 transcript 的 thinking / tool_use 块尽量重建，信息少于实时流。
4. `POST /api/sessions/{uuid}/interrupt` 停止当前会话：取消排队 turn、打断正在执行的 turn、删除租约；前端显示已停止。
5. 服务重启时 `running_sessions` 中的会话自动 resume，追加「上次任务因服务重启被中断，请检查状态并继续，不要重复已完成步骤」；没有 transcript 的租约删除，但渠道消息不会被静默丢弃：空投递行填入「服务重启时这条消息尚未开始处理，请重新发送」并在启动补发中送达；没有租约的空投递行也在启动时做同样兜底；网页来源没有投递行时写 `audit_log`；暂停/归档 Agent 的租约保留到 Agent 恢复。停机时每个缓存 client 限时断开，`deploy/agent-command-center.service` 的 `TimeoutStopSec=60` 覆盖整体关机预算。
6. 命令：`/new`、`/clear`、`/stop`、`/status`、`/help`（大小写不敏感）。网页命令由 `POST /api/chat/commands` 处理，不创建会话/turn/消息，回复以聊天流末尾临时卡片显示；`/memory` 与「整理记忆」已删除。
7. 输入框 `/` 前缀弹出命令面板：↑/↓ 选择，Enter/Tab 填入，Esc 关闭；面板只填入不执行。
8. 附件：保存到 `workspaces/<agent_id>/uploads/`，单文件 10 MB；图片支持选择、粘贴、拖拽，浏览器端缩到最长边 1600 px；发送时把文件名、相对工作区路径（`uploads/<name>`）和字节数写入用户消息，Agent 用文件工具读取。图片作为文件由 Agent 的 Read 工具查看；本版不再单独构造 `image_url` 模型输入。旧 transcript 里的绝对 `/uploads/<name>` 仍按兼容路径解析，但可能失效，优先在 `uploads/` 下查找。Agent 为 paused/archived 时上传接口返回 409；读取历史附件放行（只读）。
9. 消息操作：复制、引用；失败或停止后显示提示，用户重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成、删除单条消息。
10. assistant 消息按 Markdown 渲染（GFM、代码高亮、KaTeX `$$...$$`）；流式文本约 80ms 节流。
11. 消息不显示时间戳。同一进程内刷新或重连会在数秒内通过 `current_turn_id` 重放当前轮缓冲；进程重启或订阅断开后才丢失当前输出。
12. 网页消息只接受 active Agent；paused 与 archived 都返回 409 和中文提示。`session_id` 必须属于该 Agent：已有 transcript 必须是同一 Agent 的，否则 409；同一个 session id 不会跨 Agent 复用 client。
13. 每个 Agent 的 SDK client 缓存有上限（默认 8 个）和空闲 TTL（默认 300 秒），每 60 秒清扫；同一会话配置（instructions/Provider）变化后下一轮重建 client。

#### 验收标准

- 新建会话 → 发消息 → 流式回复 → 会话出现在列表；刷新后历史可读。
- 执行中停止 → 状态为已停止，租约删除。
- 执行中重启服务 → 会话自动 resume 并完成，`running_sessions` 清空。
- 同一会话第二个 turn 排队，不同会话可并发。
- 附件上传后可被 Agent 读取；引用以 `> ` 块进入消息。

### F3 会话目录与保留清理

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 会话只存在于 SDK transcript：`claude/projects/<agent_id>/<uuid>.jsonl`；平台不建 sessions/messages/runs 表。
2. 会话列表由 SDK `list_sessions()` 扫描全部项目目录，再按 `cwd == workspaces/<agent>` 过滤；标题取 SDK summary（最多 80 字），`last_modified` 用于排序和展示。
3. 会话历史由 `get_session_messages(session_id)` 读取；重命名、删除走 SDK（删除同时移除 transcript 与子 Agent transcript 目录）。
4. 删除会话前拒绝正在执行的会话（409）；删除开始时原子标记会话（新的提交在删除完成前被拒绝），删除完成后释放标记。删除时清理 `workspaces/<agent>/screenshots/<session_id>/`、该会话的 `channel_deliveries` 行和 `channel_conversations` 绑定。
5. 后台清理 `SessionCleanupRuntime`：启动后立即执行首轮，之后每 `SESSION_CLEANUP_INTERVAL_SECONDS`（默认 3600，最小 60）扫描；按 transcript mtime 早于保留天数（`settings.runtime.session_retention_days`，默认 30，0 关闭）删除。
6. 清理跳过：有租约的会话、registry 中忙碌或正在删除的会话、非 UUID 文件名；先清产物再删 transcript；`claude/` 下的 auto memory 不清理。
7. 微信绑定指向已删除会话时，下一条入站消息自动生成新 UUID 重新绑定；定时任务在 transcript 不存在时下次触发重新生成 session id。

#### 验收标准

- 超过保留天数的会话被清理，运行中的不被清理，`claude/projects/*/memory/` 保留。
- 删除会话后，对应截图、投递记录与渠道绑定一并清理。
- 渠道/定时任务不会因会话删除而报错，而是自动重建会话。

### F4 配置、Provider 与 Agent

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 平台配置单文件 `data/settings.json`：`runtime`、`knowledge`、`agents`、`channels`、`channel_conversations`。单 `asyncio.Lock`；写临时文件 → fsync → `os.replace`；写前保留 `.bak`；主文件损坏时回退 `.bak`。启动种子在主文件不可解析且 `.bak` 也不可用、或文件中没有合法 `agents` 列表时重写默认 sections（保留仍可读的 runtime/knowledge/channels），并记 warning；解析成功且显式 `agents: []` 的文件视为用户主动清空，保留并 warning。
2. 环境变量只做首次种子（默认 Agent 与提示词、`SESSION_RETENTION_DAYS`、`KB_WEBDAV_*`）；文件存在后不再读取。旧库 `app_configs.session_retention_days` 在首次启动迁移一次，用 `runtime.legacy_import_done` 标记。
3. Provider 为单例（id=`default`），编辑的是 default Agent 的 Provider；`base_url` 自动补 `/anthropic`；接口只返回 `has_api_key`。
4. Agent：id、名称、instructions、状态（active/paused/archived）、Provider、`auto_memory`。新建 Agent 未填 instructions 时使用默认中文助手提示词。状态语义统一为：`active` 正常接收网页、渠道和定时任务；`paused` 与 `archived` 都拒绝网页新消息（409）、拒绝渠道入站并暂停该 Agent 的渠道轮询（保留 cursor，恢复 active 后继续）、跳过定时任务；`GET /api/agents` 包含 archived，配置页可把 archived/paused 改回 active 恢复，恢复后渠道轮询自动重启、定时任务从下一次匹配时间继续；两种非 active 状态下渠道页仍可查看/编辑/删除渠道。
5. `instructions` 通过 SDK `system_prompt={type: preset, preset: claude_code, append: instructions}` 追加到 Claude Code 默认系统提示词；修改 Agent 或 Provider 后关闭该 Agent 的空闲缓存 client，忙碌会话在下一次 turn 开始时按配置指纹自动重建 client，因此配置变更不会永久丢失。
6. Agent 工作区固定 `workspaces/<agent_id>/`；归档保留工作区与 transcript，彻底删除会同时删除工作区目录和 transcript 目录，并级联删除渠道、渠道绑定、投递/入站记录与定时任务；有活动 turn 时 409。
7. 接口：`GET/PUT /api/settings`、`/api/knowledge`、`/api/agents`、`/api/providers` 读写 `settings.json`；`/api/memories` 只读写 `claude/CLAUDE.md`；`/api/skills` 读写技能目录；`/api/channels` 读写 `settings.json` 的渠道节；`/api/cron-jobs` 读写 `cron_jobs` 表；Provider 只返回 `has_api_key`，知识库只返回 `has_password`，渠道只返回 `has_credentials`。

#### 验收标准

- 新会话使用当前 Agent 的最新 instructions 与 Provider。
- 配置文件写入原子、损坏可回退；密钥不出现在接口响应和日志。
- 彻底删除 Agent 后其会话、工作区、渠道、定时任务被清理；归档只改状态，不删文件。

### F5 记忆与用户画像

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 用户画像 = `claude/CLAUDE.md`，由 SDK 作为用户级说明加载到每个 Agent；记忆页只编辑这一个 Markdown 文档，保存时保留 `CLAUDE.md.bak`，原子写入，权限 0600。文件 mtime/size 纳入 client 配置指纹，保存后下一次 turn 自动重建受影响的缓存 client。
2. Agent 指令在 `settings.json` 的 `agents[].instructions`，通过 preset+append 注入系统提示词。
3. Agent 笔记由 Claude auto memory 管理（`claude/projects/<project>/memory/`），平台不注入、不整理、不提供记忆工具；`agents[].auto_memory` 仅为兼容保留，运行时不再读取。
4. 平台记忆系统（user/agent memory 表、`/memory` 指令、整理记忆技能与工具、`memory_save` / `memory_search` / `memory_read`）全部删除。

#### 验收标准

- `claude/CLAUDE.md` 的内容在新会话中生效。
- 记忆页保存后下一会话生效，旧版本保留为 `.bak`。
- 平台不存在记忆表、记忆工具或整理记忆入口。

### F6 微信个人号渠道

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 渠道配置在 `settings.json` 的 `channels`（含 `bot_token`、`base_url`、`sync_buf`、状态、最近错误），联系人绑定在 `channel_conversations`，key 为 `"{channel_id}:{external_id}"`，存 `session_id`、`display_name`、`context_token`、`updated_at`。
2. 新建渠道后扫码登录；iLink 二维码在页面展示，登录成功后 `WeixinRuntime` 在 API 进程内为每个 active 渠道跑一个 `getupdates` 长轮询任务。
3. 入站：
   - 群聊忽略；语音/视频不下载、不落盘；纯语音/视频直接回提示且不创建 turn；混合消息只处理文字/图片/文件。
   - 图片/文件从 iLink CDN 下载、AES-128-ECB 解密后保存到 `workspaces/<agent_id>/uploads/wx-<hash>-<name>`，以附件清单写入用户消息，路径为相对工作区的 `uploads/<name>`（旧 transcript 的 `/uploads/<name>` 仍兼容解析）。
   - 同一联系人连续消息在 `CHANNEL_INBOUND_BATCH_SECONDS`（默认 5 秒，最长 15 秒）内打包成一个 turn。
   - 入站按 `(channel_id, external_message_id)` 幂等；打包窗口内不保存 `sync_buf`，崩溃后由 iLink 重放、幂等表吸收。
   - 引用回包通常只有 `msg_id` / `create_time_ms`：先按消息 id 查 `channel_inbounds`，再在目标时间前 2 秒、后 30 秒窗口内查入站文本与机器人回复；查不到写「引用原文不可用」。
   - 命令 `/new`、`/clear`、`/stop`、`/status`、`/help`：`/new` 先等待在途打包批次落库到旧会话，再 flush 待打包消息并新建 UUID 重绑；`/stop` 先等待在途批次完成，再丢弃待打包消息并中断当前会话；等待有界（30 秒），超时后回复「正在处理上一条消息，请稍后重试」并放弃本次命令，不重绑也不丢弃。`/status` 返回 Agent、会话 UUID、忙闲；命令不进 Agent、不建 turn，确认消息直接 `sendmessage`。
4. 出站：
   - turn 开始时在同一事务写 `running_sessions` 租约与 `channel_deliveries` pending 行；结束时回填最终答复并发送；取消的 turn 不发送并删除空投递行。
   - 回复按 1800 字在段落/代码围栏边界切块，最多 10 块，超出截断并标注。
   - 崩溃重启后按 pending/attempting 行补发；attempting 行补发文案为「♻️ 上一条回复可能已部分送达，以下为完整内容：」。如果一次发送在至少一块已送达后失败（含登录过期），行保持 attempting，后续补发保留该前缀；发送 claim 已计入 attempts，达到 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 后标记终态 `failed` 并停止自动补发（已知限制：前缀只在重试期间保留）。只有一块都没发出时才回到 pending。渠道暂停/登录过期时保留 pending，恢复后继续；部分送达行仍受尝试上限约束。
   - 网页里在渠道会话中手动发消息不推回微信；定时推送复用同一投递机制。
5. 运行期截图推送：工具层未接入，`channel_images.send_session_image` 已保留但当前没有调用方；下一版本接入浏览器工具后启用。
6. 登录过期（iLink `-14` 或 `-2 prepare failed`）时渠道变为「登录过期」，轮询停止；重新扫码后未发送任何分块（pending）的回复补发；部分送达行受 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 约束，达上限为 failed，不再自动补发。
7. 删除渠道：停止轮询、清空登录态、删除投递/入站记录、清理定时任务推送目标、移除渠道与联系人绑定；已产生的会话与 transcript 保留。渠道所属 Agent 非 active 时轮询暂停（保留 `sync_buf`），恢复 active 后重新启动；渠道管理接口不受 Agent 状态限制。

#### 验收标准

- 微信入站 → 5 秒打包 → 回复发回同一联系人；断网重放不重复处理。
- 微信会话 `/new`、`/stop`、`/status` 正常。
- 命令不创建 turn 或消息；确认消息可丢失但不影响会话状态。
- 服务重启后未发送回复补发；已取消的 turn 不回信。

### F7 定时任务

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. `cron_jobs` 表保存名称、prompt、5 字段 cron、IANA 时区、启用状态、`session_id`（UUID 字符串）、`next_run_at`（UTC）、`last_run_at`、`last_skipped_at`、推送目标 `push_channel_id` / `push_conversation_id`；不保存 `last_run_id` / `last_run_status`。
2. 新建任务生成一个 UUID 作为专属 SDK 会话；每次触发在同一 transcript 上入队一个 turn（`source=cron`），用户在该会话的追问共享上下文。transcript 不存在时下次触发重新生成 session id。
3. `CronRuntime` 在 API 进程内每 `CRON_TICK_SECONDS`（默认 20 秒）扫描到期任务，复用 `SessionRegistry` 与并发上限；单进程部署，多 worker 会重复触发。
4. 会话忙时跳过本次触发并写 `audit_log`（`cron_skipped`），不排队堆积；手动「立即运行」遇到忙返回 409。
5. 调度在提交 turn 前先提交数据库（消费本次计划）；提交失败写 `cron_trigger_failed` 审计，不重试。停机跨过多个触发点后每个任务最多补跑一次。
6. 暂停/归档 Agent 时不触发；恢复后从下一个匹配时间继续。暂停/删除任务不取消已产生的 turn；删除任务保留会话与 transcript。
7. 推送目标必须是当前 Agent 的 active `wechat` 渠道下已有 1:1 联系人；触发时与租约同一事务写 pending 投递，复用渠道分块、重试、补发；未配置不推送。删除渠道会清空该渠道相关任务的推送目标；删除会话或联系人只移除绑定，任务保留目标，但届时找不到绑定会静默不推送。
8. 接口：`GET/POST /api/cron-jobs`、`PUT/DELETE /api/cron-jobs/{id}`、`POST /api/cron-jobs/{id}/run`；响应为任务对象（不含 `last_run_id` / `last_run_status`）。

#### 验收标准

- 定时任务按时触发，会话忙时跳过并写审计；配置推送目标时送达微信。
- 立即运行不改变原计划；忙时返回 409。
- 删除会话后任务下次触发自动重建会话；删除任务保留历史。

### F8 技能库与工具现状

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 技能库 `~/.super-personal-platform/claude/skills/<name>/SKILL.md`（`APP_SKILLS_DIR` 可覆盖）；frontmatter 校验、来源分 `builtin` / `ai` / `manual`。
2. 内置技能 seed：`knowledge-base`、`quant-backtest`、`cron-scheduler`、`trade-journal`、`allocation`、`chanlun`（网页搜索/抓取改用 Claude Code 自带的 WebSearch/WebFetch，浏览器操作改用官方集市 `playwright` 插件，对应技能已退役）。`.seed_state.json` 记录内置内容哈希：未修改的内置技能随版本更新；用户改过的保留；已从默认列表移除且未被修改的内置技能在启动 seed 时只删除被跟踪的 `SKILL.md`，目录内用户附加文件保留，只有目录为空时才移除目录；用户改过的保留并停止跟踪（例如旧 home 的 `memory-organizer`、`web-search`、`web-fetch`、`web-browser`）。
3. 技能页按「系统技能 / 交互创建」分组，可查看和编辑 SKILL.md；技能由 Claude Code 自动发现、所有 Agent 共享，没有启用开关；非系统技能可删除（只删技能目录）。
4. 本版工具层未迁移：平台注册表中的技能、`add_dirs`、MCP、平台工具、搜索/浏览器/回测/定时/交易/资产/缠论/知识库工具都没有接入 SDK；WebFetch/WebSearch 等只是 Claude Code 默认工具集的一部分。runner 未显式设置 `tools`，默认工具集随 bundled CLI 版本和插件环境变化，以工具页/运行时 init frame 为准（例如 Read/Write/Edit/Bash/Task/WebFetch/WebSearch/Skill）。工具页按 API 展示，平台工具与技能工具标记为未接入。
5. `langchain-core` 依赖暂留，等工具层迁移完成后删除。

#### 验收标准

- 启动 seed 不覆盖用户修改的技能；被移除的内置技能在未修改时自动删除。
- 技能页可查看、编辑、启用、删除；工具页状态与实际一致。
- Agent 不会声称调用了未接入的工具。

### F9 知识库镜像

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. `KnowledgeSyncRuntime` 在 API 进程内把配置页保存的 WebDAV 目录双向同步到 `knowledge/`（`APP_KNOWLEDGE_DIR` 可覆盖）；启动后立即同步一次，之后按配置间隔（默认 300 秒）增量同步。
2. 状态存 `data/knowledge_sync_state.json`（etag + 本地哈希），只传变化文件；配置存 `settings.json` 的 `knowledge` 节，密码只写文件、接口只返回 `has_password`。
3. 冲突规则：两边都改时远端保留原名，本地另存 `<name>.conflict-local-<时间戳>` 后上传；远端删除且本地未修改时删本地；本地删除不传播；路径限制在 root 内，拒绝 `..` 与符号链接；单文件上限 50 MB。
4. 坚果云限流：按 `KB_REQUEST_INTERVAL_SECONDS`（默认 4.5 秒）限速；默认跳过 `.obsidian`、`.git` 等目录；收到 `BlockedTemporarily` / 503 时暂停约 30 分钟；列表截断或子目录读取失败时本轮跳过删除判断。
5. 远端身份（URL + 账号 + 根目录）变化时，本地镜像与状态移到 `.old-<时间戳>` 后重建。
6. 本版知识库不挂载给 Agent（技能工具层未迁移）；页面与同步正常运行。

#### 验收标准

- 配置保存后无需重启即可触发同步；同步状态可见且不泄露凭据。
- 冲突、删除、限流按上述规则处理，不误删本地文件。

### F10 量化回测

- 状态：现行（页面与后端保留，Agent 工具未接入）
- 日期：2026-11-11
- 来源：沿用旧需求，行为不变

#### 功能点

1. 功能工作区「回测」独立页面，不绑定 Agent；Bitget 现货、单标的、历史回测。
2. 行情按需抓取到 `data/market.db`（`market_candles` / `market_sync_state`），首尾增量更新，回测前修补区间缺口；补不齐的缺口保留 NaN 行，缺口期间强制空仓、跨缺口收益不计入并告警。
3. 周期 `1min` 到 `1M`；日/周/月使用 Bitget UTC 变体；只用完整 K 线。
4. 策略为页面内 Python `def strategy(df) -> pd.Series`（UTC DatetimeIndex，列 open/high/low/close/volume，返回 [-1,1] 目标仓位），在独立子进程中执行（超时/内存/进程组隔离，不是安全沙箱）。
5. `warmup_bars` 预热；截断前缀重跑检测未来函数，不一致时告警（`BACKTEST_FUTURE_CHECK` 默认开启）。
6. `BacktestWorker` 进程内串行执行；状态 `queued/fetching/running/completed/failed/cancelled`；启动时重新排队未完成任务；结果存 `backtests` 表，曲线/交易按上限截断。
7. 接口：`POST/GET /api/backtests`、`GET /api/backtests/meta`、`GET /api/backtests/{id}`、`GET /api/backtests/{id}/result`、`POST /api/backtests/{id}/cancel`、`GET /api/market/candles/status`、`GET /api/market/probe`。
8. Agent 的 `quant-backtest` 技能与 `backtest_*` 工具已注册但本版未接入 SDK。

#### 验收标准

- 页面可提交回测、查看进度/结果/取消，重启后任务恢复。
- 缺口与未来函数检测按规则告警。

### F11 交易记录

- 状态：现行（页面保留，Agent 工具未接入）
- 日期：2026-11-11

#### 功能点

1. 独立 JSONL `data/trade-journal.jsonl`（一行一笔，0600，原子替换，进程内串行写入）。
2. 支持 `crypto` / `a_share`；`status`、`return_pct`、`r_multiple`、`pnl`、`win` 由服务层计算，不落盘。
3. 页面支持筛选、新建/编辑、平仓和统计；不提供删除、图表、附件或回测导入。
4. 接口：`GET/POST /api/trade-journal`、`GET /api/trade-journal/stats`、`GET/PUT /api/trade-journal/{id}`、`POST /api/trade-journal/{id}/close`。
5. `trade-journal` 技能与五个工具已注册但本版未接入 SDK。

#### 验收标准

- 页面记录、平仓、统计正确；文件损坏行被跳过；不建数据库表。

### F12 资产配置

- 状态：现行（页面保留，Agent 工具未接入）
- 日期：2026-11-11

#### 功能点

1. 独立于交易记录：组合配置在 `data/allocation.json`（0600），流水在 `data/allocation-ledger.jsonl`（一行一笔，0600）。
2. 按时间重放流水，推导现金、移动加权成本、已实现/未实现盈亏、投入本金、总收益与 XIRR；目标合计不等于 100 只提示；超卖返回 422；有流水的组合不能改基础币种或删除。
3. `GET /api/allocation` 不联网；`POST /api/allocation/refresh` 抓 Bitget 1min 最新价、东方财富/Yahoo 美股与基金净值、USD/CNY 汇率；价格优先级 `live/stock/fund > mark > cache > cost`；刷新失败保留旧值。
4. 支持固定收益计息（年化、起息日、复投），计息标的跳过行情抓取。
5. 顶栏「总览」按 USD 折算合并全部组合（USD≈USDT），显示合并持仓与近似 XIRR。
6. 接口：`GET /api/allocation`、`POST /api/allocation/refresh`、`POST/PUT/DELETE /api/allocation/portfolios...`、`GET/POST /api/allocation/portfolios/{id}/entries`、`PUT/DELETE /api/allocation/portfolios/{id}/entries/{entry_id}`。
7. `allocation` 技能与六个工具已注册但本版未接入 SDK。

#### 验收标准

- 页面可维护组合与流水、刷新行情、查看总览与再平衡建议；失败不破坏已有价格。
- 不读取交易记录，不建数据库表。

### F13 缠论图

- 状态：现行（页面保留，Agent 工具未接入）
- 日期：2026-11-11

#### 功能点

1. 功能工作区「缠论图」，不绑定 Agent；复用 Bitget 行情缓存，只用已收盘 K 线。
2. 周期 `15min/1h/4h/1day/1week`；UTC 时间窗口，20–1000 根，默认最近 500 根。
3. 确定性算法：包含处理、严格三根分型、老笔、完整中枢、1/2/3 买卖点（带失效标记）、MACD 与简化背离；未确认走势用虚线，不计入笔数/中枢/背离/买卖点。
4. `POST /api/chan/analyze` 返回 candles/merged/fractals/strokes/pending_strokes/zones/points/macd/divergences/rules/warnings，不建表。
5. `POST /api/chan/backtest` 因果前缀回放、下一根开盘进场、固定止损/R 目标/时间止损与费用。
6. 页面参数写入 `/chan` 查询串（`replaceState`），刷新/直达恢复并自动分析。
7. `chanlun` 技能与 `chan_analyze` / `chan_backtest` 已注册但本版未接入 SDK。

#### 验收标准

- 页面可切换周期/时间窗口/显示项，MACD 与主图同步；回测输出胜率、平均 R、回撤与按信号类型统计。

### F14 前端界面、路由与响应式

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 视觉：珍珠冷光画布 + 极光弥散 + 悬浮玻璃；主色浅紫，运行态青绿到紫渐变；assistant 整列玻璃卡片，user 右对齐深色气泡。
2. 路由：自建轻量 History API（不引入路由库）；`/chat`（可选 `/chat/<session_id>`）、`/config`、`/memory`、`/skills`、`/tools`、`/plugins`、`/channels`、`/cron`、`/backtest`、`/journal`、`/allocation`、`/chan`；未知路径回聊天页。选中的 Agent 记在 `localStorage`。
3. 侧栏顶部「Agent / 功能」两个工作区：Agent 工作区按「Agent 工作台 / Agent 能力 / 平台」分组；功能工作区放回测、交易记录、资产配置、缠论图，不显示 Agent 选择器与会话。
4. ≤860px 使用顶部栏 + 汉堡菜单；侧栏变抽屉、详情变全屏面板；输入区适配 `visualViewport` 与安全区；功能页表格改卡片。
5. 会话列表由 SDK transcript 生成，显示忙碌徽标和最后修改时间；微信会话带「微信」徽标；输入区提示网页回复不会发回微信。
6. Chat 内容列（header、消息流、composer）统一 `--chat-lane` 宽度；发送时滚到底部，输出期间上滑显示「回到最新」。

#### 验收标准

- 刷新/直达/前进后退恢复页面与会话；非法 `/chan` 参数回退默认。
- 移动端可用，键盘弹起时输入框可见。

### F15 部署、升级与运维

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 生产：宿主机 systemd 用户服务 + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`（SPA fallback），没有 nginx/Postgres 容器。
2. 运行时目录 `~/.super-personal-platform/`：`claude/`（含 `skills/`、`plugins/`、`settings.json` 与 `CLAUDE.md`）、`workspaces/<agent>/`、`knowledge/`、`tmp/`、`data/{settings.json, app.db, market.db, knowledge_sync_state.json, trade-journal.jsonl, allocation.json, allocation-ledger.jsonl, browser_state.json, backtests/}`、`.env`。
3. 新库的 `app.db` 只创建六张表：`audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；从旧版升级的库会保留旧表，旧 `providers`/`agents`/`channels`/`sessions`/`messages`/`runs`/`run_events`/`memory_items` 等表不再读写，`app_configs` 仅在首次启动读取一次保留天数后不再使用。
4. 升级：本版不做数据迁移。先备份 `data/app.db` 与 `data/settings.json`；旧库可直接沿用，启动时检测并重建旧结构 `channel_deliveries` / `channel_inbounds`（旧待投递与入站去重记录丢失，iLink 可能重放一次）；旧 `providers` / `agents` / `channels` / `knowledge_configs` 配置不会导入，`settings.json` 用环境变量和默认值重新种子（仅首次启动读取旧 `app_configs.session_retention_days` 一次）；旧用户记忆与旧 `sessions/messages/runs/...` 聊天历史不会导入；旧微信渠道凭据不在 settings.json，需重新扫码绑定；旧定时任务列结构不兼容时，定时 tick 会持续记录 `cron tick failed`、任务列表接口可能 500，先备份再决定删除重建；回测历史保留。
5. 彻底重建：删除 `data/app.db` 后重启，会丢失回测历史、定时任务、审计、未完成投递与入站去重记录；`data/settings.json` 和会话 transcript 不受影响，微信绑定在 settings.json 中，不需要因此重新扫码。
6. 备份：`data/app.db`、`data/settings.json`、`claude/`、`workspaces/`、`data/trade-journal.jsonl`、`data/allocation*.json*`、`knowledge/`。
7. 旧 home 的 `memory-organizer` 技能由启动 seed 清理（未修改时删除，用户改过的保留但停止跟踪）。

#### 验收标准

- 新机器按 README 可完成安装、登录、配置 Provider、扫码绑定微信。
- 旧版本升级按 README 步骤完成，数据损失范围与文档一致。

### F16 本版不包含与已知损失

- 状态：现行
- 日期：2026-11-11

本版明确不包含：

1. 工具层迁移：技能工具、`add_dirs`、MCP、浏览器/搜索/回测/定时/交易/资产/缠论/知识库工具接入 Agent。
2. rubric 质检。
3. QuickJS eval 代码解释器。
4. 会话全文搜索。
5. 平台级多 Agent 编排/自定义子 Agent 配置（SDK 内置 Task 子 Agent 可用）。
6. 进程重启或断线后继续看当前流式输出（同进程刷新会通过 `current_turn_id` 重放当前轮缓冲）。
7. 消息时间戳。
8. Run 历史。
9. 渠道运行期截图实时推送（等工具层）。
10. `/memory` 指令与整理记忆。

已知能力损失（已接受）：进程重启或订阅断开后丢当前流式输出（同进程刷新会在数秒内从 `current_turn_id` 重放缓冲）、聊天气泡无时间戳、无会话全文搜索、失败 turn 无过程重建（用户重发即可）、排队中的追问在重启后丢失；`langchain-core` 暂留。

### F17 官方插件集市

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 插件页 `/plugins`：展示官方集市状态、已安装插件和可安装列表；支持添加、更新、移除官方集市，以及安装、启用/停用、更新、卸载插件。插件对所有 Agent 共享，没有按 Agent 的启用开关。
2. 集市来源只允许 `anthropics/claude-plugins-official`。插件以 user scope 安装到运行时 `claude/`：启用状态在 `claude/settings.json` 的 `enabledPlugins`，集市与已安装数据在 `claude/plugins/`。
3. 插件管理使用 SDK 自带的内置 CLI（与 chat 运行时同一个二进制），读取 `plugin marketplace list --json`、`plugin list --json` 和本地集市清单；启动和页面读取不联网，只有添加/更新集市与安装/更新插件才访问 GitHub。
4. 安装、启停、更新、卸载后，运行器按插件状态指纹重建受影响会话的缓存 client：新的一轮对话加载最新插件，进行中的 turn 不受影响。
5. 页面提示插件可能包含 hooks 与 MCP server、会以宿主机权限执行代码，平台不为插件代码提供沙箱；移除集市前必须先卸载该集市下的插件。

#### 验收标准

- 官方集市可添加、更新、移除；未添加时页面给出添加入口。
- 安装的插件出现在已安装列表，可启停、更新、卸载；下一轮对话能调用插件技能（`plugin:skill`）。
- CLI 缺失、GitHub 不可达、命令超时或输出不可解析时页面显示可读错误，不影响其他页面。
- 服务重启后集市与插件状态保留。
