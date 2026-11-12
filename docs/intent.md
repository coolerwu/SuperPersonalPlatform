# Intent

- 状态：现行
- 最后更新：2026-11-12
- 编号水位：F21

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
7. 定时任务：5 字段 cron、IANA 时区、专属会话、忙时跳过并更新 `last_skipped_at`、可选推送到微信联系人、停机后最多补跑一次。
8. 会话保留清理：按 transcript mtime 与保留天数删除，跳过运行中会话，保留 auto memory。
9. 记忆：记忆页有用户画像（`claude/CLAUDE.md`，跨 Agent）与 Agent 记忆（`workspaces/<agent_id>/CLAUDE.md`）；Agent 指令 = `settings.json`；Claude auto memory 在记忆页只读展示。
10. 配置页：Provider、Agent、知识库、运行环境；所有平台配置写入 `data/settings.json`，密钥字段只返回 `has_api_key` / `has_password` / `has_credentials`。
11. 技能库：全局 `claude/skills/` 目录（Claude Code 用户级技能），初始化技能 seed 与哈希跟踪；技能是说明文档，没有技能创作工具。
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
- 平台自带浏览器/搜索工具、`add_dirs`、技能创作工具接入 Agent——后续版本（平台业务工具已接入）。

### F2 执行核与网页聊天

- 状态：现行
- 日期：2026-11-11
- 来源：Claude Agent SDK 迁移

#### 功能点

1. 每条用户消息创建一个 turn，在 `SessionRegistry` 中按 session 串行执行；不同 session 并发，上限为 `BROWSER_CONCURRENCY`（默认 5）。同一会话在运行中可继续发送，新 turn 排队。
2. `POST /api/chat/messages` 入队并返回 `{session_id, turn_id, accepted}`；`GET /api/sessions/{uuid}/stream?turn_id=...` 以 SSE 转发该轮事件（`assistant_delta`、`reasoning_delta`、`tool_call`（带 tool_use id）、`subagent_text`、`subagent_tool_call`、`task_started`、`task_progress`、`task_updated`、`task_notification`、`turn_end`）。子 Agent 事件带 `parent_tool_use_id`，用于挂到对应的 Task 工具调用下；后台任务事件按 `task_id` 合并状态。
3. `GET /api/sessions/{uuid}/messages?agent_id=...` 一次性返回 transcript 中的用户/assistant 消息；执行过程从 transcript 的 thinking / tool_use 块尽量重建，信息少于实时流。子 Agent 与后台任务只实时显示，不写入主 transcript；刷新后只保留主 Agent 的执行过程（子 Agent 历史需单独读取 subagent transcript，本版未接入）。Claude Code 的中断合成消息不进聊天气泡：`[Request interrupted by user]` 返回为 `system` 行「本轮对话已被中断」，`No response requested.` 不返回。
4. `POST /api/sessions/{uuid}/interrupt` 停止当前会话：取消排队 turn、打断正在执行的 turn、删除租约；返回 `{interrupted, cancelled_turn_ids}`，前端只丢弃这些 turn 的本地占位消息；与中断竞争、未被取消的排队 turn 继续执行并保持流式订阅。未取消任何 turn 时 `interrupted=false`。
5. 服务重启时 `running_sessions` 中的会话自动 resume，追加「上次任务因服务重启被中断，请检查状态并继续，不要重复已完成步骤」；没有 transcript 的租约删除，但渠道消息不会被静默丢弃：空投递行填入「服务重启时这条消息尚未开始处理，请重新发送」并在启动补发中送达；没有租约的空投递行也在启动时做同样兜底；网页来源没有投递行时写 warning 日志；暂停/归档 Agent 的租约保留到 Agent 恢复。停机时每个缓存 client 限时断开，`deploy/agent-command-center.service` 的 `TimeoutStopSec=60` 覆盖整体关机预算。渠道消息在入站时先写 `queued` 投递行再入队；turn 开始时提升为 `pending`。排队期间服务重启或优雅关闭时，启动后由 `_answer_queued_deliveries` 填入「服务重启时这条消息尚未开始处理，请重新发送」并发送；用户 `/stop` 会丢弃排队 turn 的投递行。
6. 命令：平台处理 `/new`、`/clear`、`/stop`、`/status`、`/help`（大小写不敏感）。网页命令由 `POST /api/chat/commands` 处理，不创建会话/turn/消息，回复以聊天流末尾临时卡片显示。其余以 `/` 开头的裸命令只有出现在 Claude Code 运行时 init frame 的 `slash_commands` 中才由 `forward=true` 指示前端作为普通用户消息转发给 Agent；不在列表中的回复「未知命令」并列出当前可用命令，列表尚未加载时提示先发送一条普通消息。`GET /api/chat/slash-commands?agent_id=...` 返回该列表（排除 `terminal_slash_commands` 与平台命令重名项），按 Agent 进程内缓存，首次 turn 后可用。
7. 输入框 `/` 前缀弹出命令面板：列出平台命令和 Claude Code 运行时命令，↑/↓ 选择，Enter/Tab 填入，Esc 关闭；面板只填入不执行。
8. 附件：保存到 `workspaces/<agent_id>/uploads/`，单文件 10 MB；图片支持选择、粘贴、拖拽，浏览器端缩到最长边 1600 px；发送时把文件名、相对工作区路径（`uploads/<name>`）和字节数写入用户消息，Agent 用文件工具读取。图片作为文件由 Agent 的 Read 工具查看；本版不再单独构造 `image_url` 模型输入。旧 transcript 里的绝对 `/uploads/<name>` 仍按兼容路径解析，但可能失效，优先在 `uploads/` 下查找。Agent 为 paused/archived 时上传接口返回 409；读取历史附件放行（只读）。
9. 消息操作：复制、引用；失败或停止后显示提示，用户重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成、删除单条消息。
10. assistant 消息按 Markdown 渲染（GFM、代码高亮、KaTeX `$$...$$`）；流式文本约 80ms 节流。
11. 消息不显示时间戳。同一进程内刷新或重连会在数秒内通过 `current_turn_id` 重放当前轮缓冲；进程重启或订阅断开后才丢失当前输出。
12. 网页消息只接受 active Agent；paused 与 archived 都返回 409 和中文提示。`session_id` 必须属于该 Agent：已有 transcript 必须是同一 Agent 的，否则 409；同一个 session id 不会跨 Agent 复用 client。
13. 每个 Agent 的 SDK client 缓存有上限（默认 8 个）和空闲 TTL（默认 300 秒），每 60 秒清扫；同一会话配置（instructions/Provider）变化后下一轮重建 client。
14. 工作区文件只读下载：`GET /api/agents/{agent_id}/files/{path}` 以工作区相对路径读取 `workspaces/<agent_id>/` 下的文件；拒绝绝对路径、`..`、符号链接逃逸、目录与不存在的文件。HTML、SVG、JS、XML 等可执行脚本的类型强制以 `application/octet-stream` + `Content-Disposition: attachment` 下载，避免在平台 origin 内联渲染。
15. assistant 回复里的相对图片/文件链接按当前 Agent 工作区解析（`![图](x.png)`、`[报告](report.md)`），图片直接显示、文件可点开；外部 URL（含协议、`//`、`/api/`）与锚点保持原样。每条 assistant 回复另附「产物」列表：从 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 的 `file_path` 与截图/存 PDF 类 MCP 工具的 `filename` 提取工作区内文件；历史来自 transcript 的 tool_use 块，实时来自带 `artifact` 字段的 `tool_call` 事件；同一路径去重、后写覆盖先写；工作区外的路径不展示。

#### 验收标准

- 新建会话 → 发消息 → 流式回复 → 会话出现在列表；刷新后历史可读。
- 执行中停止 → 状态为已停止，租约删除。
- Agent 派生子 Agent 时，实时「执行过程」出现标注父 Task 工具的子 Agent 文本和工具调用；后台任务显示运行状态、最近工具和完成摘要；刷新后这些实时区消失，主 Agent 的思考/工具仍在。
- 执行中重启服务 → 会话自动 resume 并完成，`running_sessions` 清空。
- 同一会话第二个 turn 排队，不同会话可并发。
- 输入 `/` 能看到平台命令和 Claude Code 运行时命令；选择 Claude Code 命令后作为消息发送并产生 turn；不在运行时长列表中的 `/` 命令会收到「未知命令」并列出当前可用命令。
- 附件上传后可被 Agent 读取；引用以 `> ` 块进入消息。
- Agent 在工作区保存截图并在回复中用相对路径引用时，页面直接显示图片；点击可在新标签打开原文件。
- Agent 写入工作区文件后，回复下方出现「产物」列表（图片为缩略图，其他文件为文件卡片），流式过程中出现，刷新后仍在。

### F3 会话目录与保留清理

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 会话只存在于 SDK transcript：`claude/projects/<agent_id>/<uuid>.jsonl`；平台不建 sessions/messages/runs 表。
2. 会话列表由 SDK `list_sessions()` 扫描全部项目目录，再按 `cwd == workspaces/<agent>` 过滤；标题取 SDK summary（最多 80 字），`last_modified` 用于排序和展示。
3. 会话历史由 `get_session_messages(session_id)` 读取；重命名、删除走 SDK（删除同时移除 transcript 与子 Agent transcript 目录）。
4. 删除会话前拒绝正在执行的会话（409）；删除开始时原子标记会话（新的提交在删除完成前被拒绝），删除完成后释放标记。删除时清理 `workspaces/<agent>/screenshots/<session_id>/`、该会话的 `channel_deliveries` 行和 `channel_conversations` 绑定。
5. 会话保留由 Claude Code 自己执行：平台把 `settings.runtime.session_retention_days`（默认 30，0 表示不清理）写成运行时 `claude/settings.json` 的 `cleanupPeriodDays`（0 写成 36500），启动时和保存设置时同步；平台不扫描 transcript，也不再有后台清理任务。
6. 手动删除会话仍需先原子标记并拒绝新提交，再走 SDK `delete_session`，并尽力清理该会话的截图目录、`channel_deliveries` 行和 `channel_conversations` 绑定；`claude/` 下的 auto memory 不清理。
7. 微信绑定指向已删除会话时，下一条入站消息自动生成新 UUID 重新绑定，并删除旧会话遗留的 `channel_deliveries` 行（正在运行或 transcript 仍存在的会话不动）；定时任务在 transcript 不存在时下次触发重新生成 session id。

#### 验收标准

- 超过保留天数的会话由 Claude Code 按其 `cleanupPeriodDays` 清理，平台不执行清理任务；最近有活动的会话不会被命中，`claude/projects/*/memory/` 保留。
- 删除会话后，对应截图、投递记录与渠道绑定一并清理。
- 渠道/定时任务不会因会话删除而报错，而是自动重建会话；重建时旧会话的投递行被清理。

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
7. 接口：`GET/PUT /api/settings`、`/api/knowledge`、`/api/agents`、`/api/providers` 读写 `settings.json`；`GET/PUT /api/memories` 读写 `claude/CLAUDE.md`，`GET/PUT /api/memories/agents/{id}` 读写该 Agent 的 `workspaces/<id>/CLAUDE.md`，`GET /api/memories/agents/{id}/auto-memory` 只读列出 auto memory；`/api/skills` 读写技能目录；`/api/channels` 读写 `settings.json` 的渠道节；`/api/cron-jobs` 读写 `cron_jobs` 表；Provider 只返回 `has_api_key`，知识库只返回 `has_password`，渠道只返回 `has_credentials`。

#### 验收标准

- 新会话使用当前 Agent 的最新 instructions 与 Provider。
- 配置文件写入原子、损坏可回退；密钥不出现在接口响应和日志。
- 彻底删除 Agent 后其会话、工作区、渠道、定时任务被清理；归档只改状态，不删文件。

### F5 记忆、用户画像与 Agent 记忆

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 用户画像 = `claude/CLAUDE.md`，由 SDK 作为用户级说明加载到每个 Agent；记忆页编辑这个 Markdown 文档，保存时保留 `CLAUDE.md.bak`，原子写入，权限 0600。文件 mtime/size 纳入 client 配置指纹，保存后下一次 turn 自动重建受影响的缓存 client。
2. Agent 记忆 = `workspaces/<agent_id>/CLAUDE.md`，由记忆页按侧栏选中的 Agent 编辑。runner 以该工作区为 cwd 并启用 `project` setting source，CLI 把它作为项目级 CLAUDE.md 只加载给这个 Agent；保存同样保留 `.bak`、权限 0600，mtime/size 纳入 client 配置指纹。
3. Agent 指令在 `settings.json` 的 `agents[].instructions`，通过 preset+append 注入系统提示词，与 Agent 记忆是两份不同内容。
4. Claude auto memory 由 Claude 管理（`claude/projects/<project>/memory/`，`MEMORY.md` 索引 + 主题文件），记忆页只读展示，平台不写入、不整理、不提供记忆工具；单文件 128 KB、总量 1 MB、最多 200 个文件，超出部分跳过并在页面提示。`agents[].auto_memory` 仅为兼容保留，运行时不再读取。
5. 平台记忆系统（user/agent memory 表、`/memory` 指令、整理记忆技能与工具、`memory_save` / `memory_search` / `memory_read`）全部删除。

#### 验收标准

- 记忆页有两张卡片：「用户画像」跨 Agent 生效；「Agent 记忆」跟随侧栏选中的 Agent，只影响该 Agent。
- 用户画像与 Agent 记忆保存后下一轮对话生效，旧版本保留为 `.bak`；切换 Agent 时 Agent 卡片重新加载，未保存的 Agent 修改丢弃。
- Claude auto memory 在 Agent 卡片内只读展示；平台不存在记忆表、记忆工具或整理记忆入口。

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
   - turn 开始时在同一事务写 `running_sessions` 租约，并把入站时创建的 `queued` 投递行提升为 `pending`（定时推送没有 `queued` 行时在此创建 `pending` 行）；结束时回填最终答复并发送；取消的 turn 不发送并删除空投递行。入站消息在入队前创建 `queued` 投递行；排队中重启由启动兜底答复，不会静默丢失。
   - 回复按 1800 字在段落/代码围栏边界切块，最多 10 块，超出截断并标注。
   - 崩溃重启后按 pending/attempting 行补发；attempting 行补发文案为「♻️ 上一条回复可能已部分送达，以下为完整内容：」。如果一次发送在至少一块已送达后失败（含登录过期），行保持 attempting，后续补发保留该前缀；发送 claim 已计入 attempts，达到 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 后标记终态 `failed` 并停止自动补发（已知限制：前缀只在重试期间保留）。只有一块都没发出时才回到 pending。渠道暂停/登录过期时保留 pending，恢复后继续；部分送达行仍受尝试上限约束。
   - 网页里在渠道会话中手动发消息不推回微信；定时推送复用同一投递机制。
5. 运行期截图推送：平台浏览器工具未接入，`channel_images.send_session_image` 已保留但当前没有调用方；下一版本接入浏览器工具后启用。
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
4. 会话忙时跳过本次触发并更新 `cron_jobs.last_skipped_at`（定时任务页展示跳过提示），不排队堆积；手动「立即运行」遇到忙返回 409。
5. 调度在提交 turn 前先提交数据库（消费本次计划）；提交失败写 warning 日志，不重试。停机跨过多个触发点后每个任务最多补跑一次。
6. 暂停/归档 Agent 时不触发；恢复后从下一个匹配时间继续。暂停/删除任务不取消已产生的 turn；删除任务保留会话与 transcript。
7. 推送目标必须是当前 Agent 的 active `wechat` 渠道下已有 1:1 联系人；触发时与租约同一事务写 pending 投递，复用渠道分块、重试、补发；未配置不推送。删除渠道会清空该渠道相关任务的推送目标；删除会话或联系人只移除绑定，任务保留目标，但届时找不到绑定会静默不推送。
8. 接口：`GET/POST /api/cron-jobs`、`PUT/DELETE /api/cron-jobs/{id}`、`POST /api/cron-jobs/{id}/run`；响应为任务对象（不含 `last_run_id` / `last_run_status`）。

#### 验收标准

- 定时任务按时触发，会话忙时跳过并更新 `last_skipped_at`；配置推送目标时送达微信。
- 立即运行不改变原计划；忙时返回 409。
- 删除会话后任务下次触发自动重建会话；删除任务保留历史。

### F8 技能库与工具现状

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. 技能库 `~/.super-personal-platform/claude/skills/<name>/SKILL.md`；frontmatter 校验、来源分 `initial` / `ai` / `manual`。
2. 初始化模板在 `apps/api/app/seed_skills/<name>/SKILL.md`：`quant-backtest`、`chanlun`、`trade-journal`、`allocation`、`cron-scheduler`。模板按「工具清单 + 组合方式 + 硬契约」组织；平台没有给 Agent 定时任务工具，`cron-scheduler` 只说明页面入口和定时触发会话的语义。启动 seed 规则：从未初始化过的缺失技能写入；未修改（哈希等于 `.seed_state.json` 记录）的技能随版本更新；改过的永久跳过；用户删除后保留记录作墓碑，只要模板仍在就不再生成；模板移除时只删记录、保留本地文件；保留的副本不再计入「初始化技能」，页面归入「其他技能」。
3. 技能页按「初始化技能 / 其他技能」分组，可查看和编辑 SKILL.md；技能由 Claude Code 自动发现、所有 Agent 共享，没有启用开关；任何技能都可删除（只删技能目录），初始化技能删除后不复活；改过的初始化技能标「已调整」。
4. 平台业务工具已通过进程内 MCP 接入 chat：回测 4 个（`backtest_*`）、行情 2 个（`market_*`）、交易记录 5 个（`trade_journal_*`）、资产配置 6 个（`allocation_*`）、缠论 2 个（`chan_analyze` / `chan_backtest`），共 19 个。runner 为每个缓存的 SDK client 挂一个 `platform_tools` SDK MCP server，工具以 `mcp__platform_tools__*` 出现在 init frame；插件自带的 MCP 继续并存。server 配置 `timeout`（至少 180 秒）和 `disableAutoBackground`，覆盖 `backtest_run` 最长约 90 秒的等待。交易记录与资产配置的写入工具对 Agent 开放。
5. 工具页读取当前 Agent 最近一轮对话的运行时 init frame：`tools` 按内置 / `mcp__<server>__<tool>` 分类，`mcp_servers` 展示连接状态与来源，`skills`、`plugins`、`plugin_errors` 分别展示；平台业务工具的描述来自注册表。平台不再维护 Claude Code 工具清单；没有运行时数据时提示先发一条消息。平台自带的浏览器工具、`web_search`/`web_fetch` 与 `skill_save` 仍未接入；浏览器使用官方集市 `playwright` 插件，搜索使用 Claude Code 自带的 WebSearch/WebFetch。
6. `langchain-core` 暂留：业务工具仍以 `StructuredTool` 注册，runner 把它们适配成 SDK MCP 工具。

#### 验收标准

- 启动 seed 不覆盖已修改（含 Agent 修改）的初始化技能；用户删除后不复活；模板移除时本地文件保留。
- 技能页正确显示来源与「已调整」，初始化技能可删除；工具页展示的内容与该 Agent 最近一轮 init frame 一致。
- Agent 只能调用运行时 init frame 中实际出现的工具；平台业务工具以 `mcp__platform_tools__*` 出现。

### F9 知识库镜像

- 状态：现行
- 日期：2026-11-11

#### 功能点

1. `KnowledgeSyncRuntime` 在 API 进程内把配置页保存的 WebDAV 目录双向同步到 `knowledge/`（`APP_KNOWLEDGE_DIR` 可覆盖）；启动后立即同步一次，之后按配置间隔（默认 300 秒）增量同步。
2. 状态存 `data/knowledge_sync_state.json`（etag + 本地哈希 + 待确认删除标记），只传变化文件；配置存 `settings.json` 的 `knowledge` 节，密码只写文件、接口只返回 `has_password`。
3. 冲突规则：两边都改时远端保留原名，本地另存 `<name>.conflict-local-<时间戳>` 后上传；远端删除且本地未修改时删本地；本地删除在连续两次同步都确认文件仍缺失、且远端在此期间未变化时才传播到远端（第一次只记录待确认，既不删除也不恢复），期间远端有改动则远端优先并恢复本地；单轮待传播删除达到 20 个且超过已跟踪文件一半时视为本地镜像事故，不传播并恢复本地。路径限制在 root 内，拒绝 `..` 与符号链接；单文件上限 50 MB。
4. 坚果云限流：按 `KB_REQUEST_INTERVAL_SECONDS`（默认 4.5 秒）限速；默认跳过 `.obsidian`、`.git` 等目录；收到 `BlockedTemporarily` / 503 时暂停约 30 分钟；列表截断或子目录读取失败时本轮跳过删除判断。
5. 远端身份（URL + 账号 + 根目录）变化时，本地镜像与状态移到 `.old-<时间戳>` 后重建。
6. 本版知识库不挂载给 Agent（知识库工具未接入）；页面与同步正常运行。

#### 验收标准

- 配置保存后无需重启即可触发同步；同步状态可见且不泄露凭据。
- 冲突、删除、限流按上述规则处理，不误删本地文件。
- 本地删除或移动在两个同步周期内传播到远端；批量删除不传播，本地副本被恢复。

### F10 量化回测

- 状态：现行（页面与后端；Agent 工具已接入）
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
8. Agent 可使用 `quant-backtest` 初始化技能；`backtest_*` / `market_*` 工具已通过进程内 `platform_tools` MCP 接入。

#### 验收标准

- 页面可提交回测、查看进度/结果/取消，重启后任务恢复。
- 缺口与未来函数检测按规则告警。

### F11 交易记录

- 状态：现行（页面保留；Agent 工具已接入）
- 日期：2026-11-11

#### 功能点

1. 独立 JSONL `data/trade-journal.jsonl`（一行一笔，0600，原子替换，进程内串行写入）。
2. 支持 `crypto` / `a_share`；`status`、`return_pct`、`r_multiple`、`pnl`、`win` 由服务层计算，不落盘。
3. 页面支持筛选、新建/编辑、平仓和统计；不提供删除、图表、附件或回测导入。
4. 接口：`GET/POST /api/trade-journal`、`GET /api/trade-journal/stats`、`GET/PUT /api/trade-journal/{id}`、`POST /api/trade-journal/{id}/close`。
5. Agent 可使用 `trade-journal` 初始化技能；五个 `trade_journal_*` 工具已通过进程内 `platform_tools` MCP 接入。

#### 验收标准

- 页面记录、平仓、统计正确；文件损坏行被跳过；不建数据库表。

### F12 资产配置

- 状态：现行（页面保留；Agent 工具已接入）
- 日期：2026-11-11

#### 功能点

1. 独立于交易记录：组合配置在 `data/allocation.json`（0600），流水在 `data/allocation-ledger.jsonl`（一行一笔，0600）。
2. 按时间重放流水，推导现金、移动加权成本、已实现/未实现盈亏、投入本金、总收益与 XIRR；目标合计不等于 100 只提示；超卖返回 422；有流水的组合不能改基础币种或删除。
3. `GET /api/allocation` 不联网；`POST /api/allocation/refresh` 抓 Bitget 1min 最新价、东方财富/Yahoo 美股与基金净值、USD/CNY 汇率；价格优先级 `live/stock/fund > mark > cache > cost`；刷新失败保留旧值。
4. 支持固定收益计息（年化、起息日、复投），计息标的跳过行情抓取。
5. 顶栏「总览」按 USD 折算合并全部组合（USD≈USDT），显示合并持仓与近似 XIRR。
6. 接口：`GET /api/allocation`、`POST /api/allocation/refresh`、`POST/PUT/DELETE /api/allocation/portfolios...`、`GET/POST /api/allocation/portfolios/{id}/entries`、`PUT/DELETE /api/allocation/portfolios/{id}/entries/{entry_id}`。
7. Agent 可使用 `allocation` 初始化技能；六个 `allocation_*` 工具已通过进程内 `platform_tools` MCP 接入。

#### 验收标准

- 页面可维护组合与流水、刷新行情、查看总览与再平衡建议；失败不破坏已有价格。
- 不读取交易记录，不建数据库表。

### F13 缠论图

- 状态：现行（页面保留；Agent 工具已接入）
- 日期：2026-11-11

#### 功能点

1. 功能工作区「缠论图」，不绑定 Agent；复用 Bitget 行情缓存，只用已收盘 K 线。
2. 周期 `15min/1h/4h/1day/1week`；UTC 时间窗口，20–1000 根，默认最近 500 根。
3. 确定性算法：包含处理、严格三根分型、老笔、完整中枢、1/2/3 买卖点（带失效标记）、MACD 与简化背离；未确认走势用虚线，不计入笔数/中枢/背离/买卖点。
4. `POST /api/chan/analyze` 返回 candles/merged/fractals/strokes/pending_strokes/zones/points/macd/divergences/rules/warnings，不建表。
5. `POST /api/chan/backtest` 因果前缀回放、下一根开盘进场、固定止损/R 目标/时间止损与费用。
6. 页面参数写入 `/chan` 查询串（`replaceState`），刷新/直达恢复并自动分析。
7. Agent 可使用 `chanlun` 初始化技能；`chan_analyze` / `chan_backtest` 已通过进程内 `platform_tools` MCP 接入。

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
3. 新库的 `app.db` 只创建五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；从旧版升级的库会保留旧表，旧 `providers`/`agents`/`channels`/`sessions`/`messages`/`runs`/`run_events`/`memory_items` 等表不再读写，`app_configs` 仅在首次启动读取一次保留天数后不再使用。
4. 升级：本版不做数据迁移。先备份 `data/app.db` 与 `data/settings.json`；旧库可直接沿用，启动时检测并重建旧结构 `channel_deliveries` / `channel_inbounds`（旧待投递与入站去重记录丢失，iLink 可能重放一次）；旧 `providers` / `agents` / `channels` / `knowledge_configs` 配置不会导入，`settings.json` 用环境变量和默认值重新种子（仅首次启动读取旧 `app_configs.session_retention_days` 一次）；旧用户记忆与旧 `sessions/messages/runs/...` 聊天历史不会导入；旧微信渠道凭据不在 settings.json，需重新扫码绑定；旧定时任务列结构不兼容时，定时 tick 会持续记录 `cron tick failed`、任务列表接口可能 500，先备份再决定删除重建；回测历史保留。启动时会把逐字节等于旧默认的 Agent instructions 更新为新默认（用户改过的保留，写出保留 `.bak`）。
5. 彻底重建：删除 `data/app.db` 后重启，会丢失回测历史、定时任务、未完成投递与入站去重记录；`data/settings.json` 和会话 transcript 不受影响，微信绑定在 settings.json 中，不需要因此重新扫码。
6. 备份：`data/app.db`、`data/settings.json`、`claude/`、`workspaces/`、`data/trade-journal.jsonl`、`data/allocation*.json*`、`knowledge/`。
7. 旧 home 里遗留的已退役技能（如 `memory-organizer`、`knowledge-base`）不再由启动 seed 删除：模板移除时只删 `.seed_state.json` 记录，本地文件保留，由用户自行清理。

#### 验收标准

- 新机器按 README 可完成安装、登录、配置 Provider、扫码绑定微信。
- 旧版本升级按 README 步骤完成，数据损失范围与文档一致。

### F16 本版不包含与已知损失

- 状态：现行
- 日期：2026-11-11

本版明确不包含：

1. 平台自带浏览器/搜索工具、`add_dirs`、技能创作工具、知识库工具接入 Agent；平台业务工具已通过进程内 MCP 接入。
2. rubric 质检。
3. QuickJS eval 代码解释器。
4. 会话全文搜索。
5. 平台级多 Agent 编排/自定义子 Agent 配置（SDK 内置 Task 子 Agent 可用）。
6. 进程重启或断线后继续看当前流式输出（同进程刷新会通过 `current_turn_id` 重放当前轮缓冲）。
7. 消息时间戳。
8. Run 历史。
9. 渠道运行期截图实时推送（等平台浏览器工具接入）。
10. `/memory` 指令与整理记忆。
11. 过期会话的 transcript 由 Claude Code 删除，但平台不再随保留期清理其投递历史与旧截图目录；渠道绑定在下次入站时重建并清掉旧投递行。

已知能力损失（已接受）：进程重启或订阅断开后丢当前流式输出（同进程刷新会在数秒内从 `current_turn_id` 重放缓冲）、聊天气泡无时间戳、无会话全文搜索、失败 turn 无过程重建（用户重发即可）、排队中的追问在重启后丢失、过期会话的投递历史与旧截图目录不再自动清理、子 Agent 与后台任务只实时可见且刷新后不保留；`langchain-core` 暂留。

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

### F18 删除审计日志

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 新库不再创建 `audit_log` 表，删除全部写入路径（`AuditLog` 模型、定时任务跳过/提交失败写入、重启恢复丢轮写入）；新库 `app.db` 只创建五张表：`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`。
2. 原审计信息由现有可见性替代：定时任务忙时跳过只更新 `cron_jobs.last_skipped_at`（定时任务页展示「上次触发因会话忙被跳过」）；定时提交失败与网页丢轮各写一条 warning 日志。
3. 已存在的库不迁移：`audit_log` 表原样保留（平台不再读写）。

#### 验收标准

- 代码中没有 `AuditLog` 模型与审计写入，测试不再断言审计行。
- 新库只有五张表；定时任务页仍显示跳过提示；提交失败与网页丢轮在日志中有 warning。
- 后端测试与前端构建通过。

### F19 知识库同步传播本地删除

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 本地删除或移动文件后，同步把删除传播到 WebDAV 远端；移动表现为远端旧路径删除、新路径上传。
2. 首次发现已同步文件本地缺失时只记录「待确认」，不删除也不恢复；第二次同步仍缺失且远端未变化才删除远端。远端在此期间有变化时保留远端并恢复本地。
3. 单轮待传播删除达到 20 个且超过已跟踪文件一半时视为本地镜像事故：不删除远端，恢复本地副本并记 warning。
4. 删除确认状态存在 `knowledge_sync_state.json`；远端删除失败计入错误并在下一轮重试；下载失败不留下删除依据。

#### 验收标准

- 本地删除一个已同步文件，两个同步周期后远端文件消失，本地不复活。
- 本地移动一个已同步文件，两个周期后远端只剩新路径。
- 一次删除 20 个以上且超过一半已跟踪文件时远端不变，本地副本被恢复。
- 远端删除、冲突、限流等原有规则不变；后端测试与前端构建通过。

### F20 中断后的消息可见性

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 中断接口返回被取消的 turn id 列表；网页只移除这些 turn 的本地占位消息，与中断竞争、未被取消的排队追问继续显示并继续流式跟随。
2. transcript 重建时把 `[Request interrupted by user]` 显示为系统行「本轮对话已被中断」，不显示 `No response requested.`；系统行没有复制/引用操作。

#### 验收标准

- `/stop` 后追问气泡不消失；被真正取消的 turn 的占位消息被移除，排队中的追问按原顺序继续执行。
- 历史里中断标记显示为系统行，`No response requested.` 不再出现。
- 后端测试与前端构建通过。

### F21 前端流畅度与聊天跟随

- 状态：现行
- 日期：2026-11-12

#### 功能点

1. 聊天视图在发送、流式输出和排队追问期间自动跟随最新内容；只有用户主动上滚才暂停，点「回到最新」恢复。
2. 消息行与消息列表按引用 memo；历史消息不随流式刷新重新渲染，输入区状态变化不重渲染消息列表。
3. 侧栏轮询结果逐字段比较，内容未变化时不触发页面重渲染。
4. 视觉风格（毛玻璃、极光、珍珠冷光）保持不变。
5. 聊天历史消息列表虚拟化，只渲染可视区附近的窗口；浏览器 Ctrl+F 只能命中已渲染的消息。

#### 验收标准

- 长会话（300 条消息以上）发送后视图贴底，流式持续跟随，主动上滚后出现「回到最新」。
- 空闲时侧栏轮询不再引起整树重渲染。
- 后端测试与前端构建通过。
