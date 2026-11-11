# Agent Command Center

v1：单用户自托管的网页对话助手，执行核为 Claude Agent SDK（`ClaudeSDKClient`），模型走 DeepSeek 的 Anthropic 兼容端点。包含多 Agent 配置、微信个人号渠道、定时任务、技能库、知识库镜像，以及不绑定 Agent 的量化回测、交易记录、资产配置和缠论图页面。

- `apps/api`：FastAPI + Claude Agent SDK（Python）
- `apps/web`：React + Vite + TypeScript
- `deploy/`：systemd unit 与 `.env` 模板（生产）
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`
- 会话与消息由 SDK transcript 管理；平台配置在 `data/settings.json`；新库的 `app.db` 只创建 `audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 六张表（从旧版升级的库会保留未使用的旧表）

本版明确不包含：平台自带浏览器/搜索工具、`add_dirs`、技能创作工具、知识库工具接入 Agent；rubric 质检、QuickJS eval、会话全文搜索、平台级多 Agent 编排/自定义子 Agent 配置（SDK 内置 Task 子 Agent 可用）、进程重启后继续看当前输出、消息时间戳、Run 历史。平台业务工具（回测、行情、交易记录、资产配置、缠论）已通过进程内 MCP 接入。同一进程内刷新会在数秒内重放当前轮缓冲；进程重启或断线才丢失。`langchain-core` 暂留，用于工具包装。

## 运行时目录

配置与数据统一在 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：

```text
~/.super-personal-platform/
├── .env                    # APP_AUTH_TOKEN、LLM_* 等配置
├── claude/                 # CLAUDE_CONFIG_DIR（权限 0700）
│   ├── CLAUDE.md           # 用户画像，跨 Agent 生效（记忆页编辑）
│   ├── settings.json       # Claude Code 自身配置（插件启用状态等）
│   ├── skills/             # 全局技能库（.seed_state.json 记录初始化技能哈希）
│   ├── plugins/            # 官方集市与已安装插件（插件页管理）
│   └── projects/<agent_id>/
│       ├── <uuid>.jsonl    # SDK 会话 transcript
│       └── memory/         # Claude auto memory
├── workspaces/<agent_id>/  # SDK 的 cwd；uploads/、screenshots/、CLAUDE.md（Agent 记忆）
├── knowledge/              # 坚果云知识库的本地镜像
├── tmp/                    # SDK 的 TMPDIR
└── data/
    ├── settings.json       # 平台配置（0600，写前留 .bak）
    ├── app.db              # SQLite（新库六张表；升级库保留旧表）
    ├── market.db           # Bitget 行情缓存
    ├── knowledge_sync_state.json
    ├── trade-journal.jsonl
    ├── allocation.json
    ├── allocation-ledger.jsonl
    ├── browser_state.json  # 浏览器登录态（平台浏览器工具接入后使用）
    └── backtests/          # 回测策略临时文件
```

启动目录下的 `.env` 会覆盖 home 配置，便于本地临时覆盖。`data/settings.json` 是平台配置，`claude/settings.json` 是 Claude Code 自身配置，两者无关。

## 本地开发

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e "apps/api[dev]"

cd apps/api
../../.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd apps/web
npm install
npm run dev
```

打开 `http://localhost:5173`，默认 token 见 `APP_AUTH_TOKEN`（默认 `dev-token`）。本地聊天需要有效模型密钥：通过 `export LLM_API_KEY=...`、`~/.super-personal-platform/.env` 或启动目录 `.env` 配置，也可以登录后在「配置」页填写；不要提交。

## Chat

- 执行核为 `ClaudeSDKClient`：每个平台会话一个 UUID，直接作为 SDK `session_id`；transcript 在 `claude/projects/<agent_id>/<uuid>.jsonl`。同一会话的 turn 串行，不同会话并发（上限 `BROWSER_CONCURRENCY`，默认 5）。
- 流式输出、停止生成、运行中排队追问：发送返回 `{session_id, turn_id, accepted}`，前端订阅 `GET /api/sessions/{uuid}/stream?turn_id=...`；运行中继续发送会排队，当前 turn 结束后按顺序执行。
- 刷新或断线后当前流式输出可能丢失：同一进程内前端会在数秒内通过 `current_turn_id` 重新订阅并重放当前轮缓冲；进程重启或 SSE 断开且错过缓冲时只能从 transcript 读回历史，执行过程从 thinking / tool_use 块尽量重建，信息少于实时流。消息不显示时间戳。
- `POST /api/sessions/{uuid}/interrupt` 停止当前会话：取消排队 turn、打断正在执行的 turn、删除租约；前端显示「已停止」。
- 服务重启时，`running_sessions` 中的会话会自动 resume，并收到「上次任务因服务重启被中断，请检查状态并继续，不要重复已完成步骤」的提示。没有 transcript 的租约删除；渠道消息的空投递行会填入「服务重启时这条消息尚未开始处理，请重新发送」并由启动补发送达，没有租约的空投递行也在启动时做同样兜底；网页来源写 `audit_log`。暂停/归档 Agent 的租约保留到 Agent 恢复。缓存 client 在停机时逐个限时断开，`TimeoutStopSec=60` 覆盖整体停机预算。
- 命令：`/new`（或 `/clear`）、`/stop`、`/status`、`/help`，不区分大小写。网页命令由 `POST /api/chat/commands` 处理，不创建会话/turn/消息，回复以聊天流末尾的临时卡片显示；输入框以 `/` 开头时弹出命令面板，支持前缀过滤、↑/↓ 选择、Enter/Tab 填入、Esc 关闭。
- `CLAUDE.md` 用户画像由 SDK 作为用户级说明加载；Agent 记忆是工作区的项目级 `CLAUDE.md`，只加载给该 Agent；Agent 自己的 instructions 追加到 Claude Code 默认系统提示词。修改后，空闲会话的缓存 client 会在保存 Agent/Provider 时关闭，下一个 turn 生效。
- assistant 消息按 Markdown 渲染：GFM 表格/列表、代码高亮、链接、`$$...$$` 数学公式（KaTeX；单 `$` 按普通文本处理）。
- 消息操作：复制、引用；按钮位于气泡下方，引用内容显示在输入框上方并可单独取消，发送时以 `> ` 引用块写入消息；失败或停止后显示提示，重新发送内容即可，没有单独的重试按钮。不做编辑、重新生成或删除单条消息。
- 附件：保存到 `workspaces/<agent_id>/uploads/`，单文件 10 MB；支持选择、粘贴、拖拽图片，大图在浏览器端缩到最长边 1600 px。发送时把文件名、相对工作区路径（`uploads/<name>`）和字节数写入用户消息，Agent 用文件工具按 SDK cwd 解析；旧 transcript 的 `/uploads/<name>` 仍兼容解析。图片由 Agent 用 Read 工具查看。上传文件通过 `GET /api/agents/{agent_id}/uploads/{filename}` 读取；Agent 为 paused/archived 时上传返回 409，读取历史附件仍放行（只读）。
- Agent 产物：回复里的相对图片/文件链接按当前 Agent 工作区解析，图片直接显示、文件可点开；文件通过 `GET /api/agents/{agent_id}/files/{path}` 只读读取，HTML/SVG/JS/XML 等类型强制下载。回复下方另列「产物」（来自 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 与截图/存 PDF 类 MCP 工具），同一路径去重；工作区外文件与 MCP 直接返回的图片不在列表中。
- 草稿、附件和引用按会话隔离（key 为 `agent_id + session_id`，未创建会话时用 `__new__`）；切换会话恢复各自内容。发送时强制滚到底部，输出期间上滑显示「回到最新」。
- 会话列表由 SDK `list_sessions()` 生成：标题取 SDK summary，按最后修改时间倒序；微信会话带「微信」徽标，忙碌会话显示「忙碌」。删除会话前会拒绝正在执行的会话。
- 空状态提供建议提示词；顶部轻量栏显示当前 Agent 和会话标题。
- 响应式：桌面为侧栏 + 对话；窄屏（≤860px）用顶部栏和汉堡菜单打开侧栏抽屉，输入区适配 `visualViewport` 与安全区。功能工作区（回测、交易记录、资产配置、缠论图）同样使用顶部栏和抽屉。

## 配置页

侧栏「平台 → 配置」进入配置页，顶部用 `Provider / Agent / 知识库 / 运行环境` 四个 tab 切换。

- **Provider**：单例（id `default`），编辑 default Agent 的 Provider（base_url 自动补 `/anthropic`、model、api_key）；接口只返回 `has_api_key`。保存后关闭该 Agent 的空闲缓存 client，下一个 turn 生效。
- **Agent**：列出全部 Agent 摘要（Provider/model、记忆开关、状态，含「已归档」），点击行切换全局选中的 Agent；编辑名称、instructions、Provider、状态；新建 Agent 未填 instructions 时使用默认中文个人助手提示词。Agent 工作区固定 `workspaces/<agent_id>/`。状态语义：`active` 正常接收网页、渠道与定时任务；`paused` 和 `archived` 都拒绝网页新消息（409）、暂停渠道轮询（保留 cursor）并跳过定时任务；两种非 active 状态下渠道页仍可查看、编辑和删除渠道；在配置页把状态改回 `active` 即可恢复（archived 也会重新出现在列表里），微信轮询随状态变更自动重启。
- **知识库**：启用开关、WebDAV 地址、账号、应用密码、根目录、同步间隔；密码只写入 `settings.json`，接口只返回 `has_password`。
- **运行环境**：版本、执行核、数据库类型、单轮执行超时（`RUN_TIMEOUT_SECONDS`）、transcript 计数和能力开关（`GET /api/system`）；可设置会话保留天数（默认 30，0 不清理，`GET/PUT /api/settings`）。平台把它写成 Claude 的 `cleanupPeriodDays`，由 Claude 自己清理 transcript，平台不跑清理任务。
- Agent 彻底删除：有活动 turn 时 409；确认后删除该 Agent 的 transcript、工作区目录、渠道与绑定、定时任务、投递/入站记录和渠道登录态。归档只改状态，不删文件，之后可在配置页把状态改回 active 恢复。

所有 per-agent 配置都以左上角侧边栏的 Agent 状态为唯一来源；配置页的 Agent 列表只能切换这个全局选中的 Agent，不持有第二套选择状态。

## 技能、工具与插件页面

侧栏顶部是「Agent / 功能」两个工作区 tab（默认 Agent）。Agent 工作区按「Agent 工作台 / Agent 能力 / 平台」分组：Agent 工作台放 Agent 选择器、新对话和会话列表；「Agent 能力」组提供技能、工具、插件、渠道、定时、记忆；「平台」组放配置和退出登录。功能工作区放回测、交易记录、资产配置、缠论图，不显示 Agent 选择器和会话。

- **技能**：列出全局技能库，按「初始化技能」和「其他技能」分组；技能由 Claude Code 自动发现、所有 Agent 共享，没有启用开关。展示 description、来源徽标、「已调整」标记、声明的工具、SKILL.md 路径和解析警告；点「查看」可打开 SKILL.md 并直接编辑保存；任何技能都可删除（只删技能目录）。
- **工具**：如实展示当前接入状态。平台业务工具（`backtest_*`、`market_*`、`trade_journal_*`、`allocation_*`、`chan_analyze`/`chan_backtest`，共 19 个）已通过进程内 `platform_tools` MCP 接入，以 `mcp__platform_tools__*` 出现在运行时 init frame；插件自带的 MCP 继续并存。平台自带的浏览器工具、`web_search`/`web_fetch` 与 `skill_save` 仍未接入：浏览器使用官方集市 `playwright` 插件，搜索使用 Claude Code 自带的 WebSearch/WebFetch。runner 未显式限制 `tools`，其余默认工具随 bundled CLI 版本和插件环境变化。
- **插件**：管理 Claude Code 官方集市与插件，见下节。
- 初始化技能模板在 `apps/api/app/seed_skills/`：`quant-backtest`、`cron-scheduler`、`trade-journal`、`allocation`、`chanlun`。技能只是初始化参考：`.seed_state.json` 记录初始化内容哈希；从未初始化过的缺失技能写入；未修改的技能随版本更新；改过的永久跳过；用户删除后保留记录作墓碑，只要模板仍在就不再生成；模板移除时只删记录、保留本地文件；保留的副本不再计入「初始化技能」，页面归入「其他技能」。

### 插件集市

- 只接入官方集市 `anthropics/claude-plugins-official`；插件以 user scope 安装，写入运行时 `claude/settings.json` 的 `enabledPlugins` 和 `claude/plugins/`，所有 Agent 共享，没有按 Agent 的启用开关。
- 平台调用 SDK 自带的内置 CLI（与 chat 运行时同一个二进制，随 venv 安装）执行 `claude plugin` 命令；启动和页面读取不联网，只有添加/更新集市与安装/更新插件需要访问 GitHub。添加集市会克隆官方仓库、安装/更新插件会先刷新集市，慢链路可能需要数分钟。
- 安装、启停、更新或卸载插件后，下一轮对话会重建受影响会话的缓存 client 并加载最新插件；进行中的 turn 不受影响。
- 插件可能包含 skills、命令、hooks 与 MCP server，其中 hooks/MCP 会以宿主机用户权限执行代码，平台不提供沙箱。只安装可信插件；移除集市前必须先卸载其下的插件。
- 官方集市的 `playwright` 插件（Microsoft，`npx @playwright/mcp`）用于浏览器操作；启用后 CLI 会自动加载它自带的 MCP server，平台自带的 `browser_tools` 仍未接入 SDK。

### Skills 文件格式

全局技能库存放在 `~/.super-personal-platform/claude/skills/`，每个技能一个目录：

```text
claude/skills/
└── <skill-name>/
    ├── SKILL.md
    └── 其他辅助文件（可选）
```

`SKILL.md` 必须有 YAML frontmatter，`name` 建议与目录名一致；不一致时技能页会告警，但仍可保存。

```markdown
---
name: example-skill
description: 什么时候应该使用这个技能
metadata:
  include_tools: some_tool another_tool
---

# 技能正文
```

`metadata.include_tools` 必须是空格分隔的工具名。这些声明只用于技能页展示，工具是否可用以工具页和运行时 init frame 为准。

## 记忆

侧栏「Agent 能力 → 记忆」有两张卡片，跟随左上角选中的 Agent：

- **用户画像**：`~/.super-personal-platform/claude/CLAUDE.md`，由 SDK 作为用户级说明加载到每个 Agent，跨 Agent 生效。保存时旧版本保留为同目录 `CLAUDE.md.bak`，文件权限 0600；文件 mtime/size 纳入 client 配置指纹，下一次 turn 自动重建受影响的缓存 client（已有会话下一次发消息即生效）。
- **Agent 记忆**：`~/.super-personal-platform/workspaces/<agent_id>/CLAUDE.md`，作为项目级 CLAUDE.md 只加载给当前 Agent。保存同样保留 `.bak`、权限 0600，mtime/size 纳入 client 配置指纹；切换 Agent 时卡片重新加载，未保存的修改丢弃。
- **Claude auto memory**：`claude/projects/<agent_id>/memory/`（`MEMORY.md` 索引 + 主题文件），由 Claude 在对话中自动维护；记忆页只读展示，单文件 128 KB、总量 1 MB、最多 200 个文件，平台不写入、不整理。
- **Agent 指令**：在 `settings.json` 的 `agents[].instructions`，通过 preset+append 追加到系统提示词，和 Agent 记忆是两份不同内容。
- 平台不再提供用户/Agent 记忆表、`/memory` 指令、整理记忆技能或记忆工具。

## 渠道（微信个人号）

侧栏「Agent 能力 → 渠道」按当前 Agent 管理渠道；一个 Agent 可以绑定多个微信渠道。

- 新建渠道后立即显示 iLink 二维码；用微信扫码并在手机上确认，状态变为「已连接」。登录成功后，平台在 API 进程内为该渠道启动一个 `getupdates` 长轮询任务；不需要公网入口、Webhook、Node 或第三方网关。
- 每个与 bot 私聊的联系人映射为一个独立 SDK 会话（侧栏带「微信」徽标），在网页端可见并可继续对话；群聊忽略。
- 入站支持文字、图片、文件与文本引用：图片/文件从 iLink CDN 下载、AES-128-ECB 解密后保存到该 Agent 的 `uploads/`，并以附件清单进入会话（消息里是相对路径 `uploads/<name>`）；引用回包通常不含原文，平台按 `msg_id` / `create_time_ms` 在本地入站记录与机器人回复中反查，查不到时标注「引用原文不可用」。语音/视频不下载、不落盘；纯语音/视频直接回固定提示且不创建对话，混合消息只处理文字/图片/文件。Agent 为 paused/archived 时该 Agent 的渠道轮询暂停并保留 `sync_buf`，恢复 active 后继续；渠道页仍可查看、编辑和删除这些渠道。
- 同一联系人的连续消息在 5 秒窗口内合并成一个 turn（最长 15 秒）；打包窗口内不保存 `sync_buf`，服务重启时由 iLink 重放消息，重复消息按 `external_message_id` 幂等吸收。
- 回复在 turn 结束时立即发送，按 1800 字在段落/代码围栏边界切块（最多 10 块，超出截断并标注）；发送失败按 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 重试。服务崩溃后启动时补发未完成回复；`attempting` 行补发时加「♻️ 上一条回复可能已部分送达，以下为完整内容：」前缀。一次发送在至少一块已送达后失败（含登录过期）也保持 `attempting`，后续补发同样带前缀；发送 claim 已计入 attempts，达到 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 后标记终态 `failed`、不再自动补发（已知限制：部分送达前缀只在重试期间保留）。只有一块都没发出才回到 pending。取消的 turn 不回信。
- 微信里可以发命令：`/new`（或 `/clear`）为当前联系人开始新会话（旧会话保留）、`/stop` 取消并丢弃待打包消息、`/status` 查看 Agent/会话/忙闲、`/help`。命令不经过 Agent、不产生消息或 turn，确认回复直接通过 iLink 发送（进程崩溃时可能丢失确认）。上一批仍在入库时，命令最多等待 30 秒；超时回复「正在处理上一条消息，请稍后重试」，本次 `/new` 不重绑、`/stop` 不取消。
- 网页聊天框支持同一套命令；网页里在渠道会话中手动发消息不会推回微信。
- 登录过期（iLink `-14` 或 `-2 prepare failed`）时渠道变为「登录过期」，轮询停止；点「重新扫码」重新登录，未发送任何分块（pending）的回复在恢复后补发；部分送达行（attempting）仍受 `CHANNEL_DELIVERY_MAX_ATTEMPTS` 约束，达到上限后为 `failed`，不再自动补发。
- 渠道暂停后停止收发；删除渠道会删除登录凭据、渠道绑定、入站/投递记录并清空定时任务的推送目标，已产生的会话与 transcript 保留。
- `bot_token` 只存在 `data/settings.json`，接口不返回明文；请保护该文件权限。
- 运行期 `browser_screenshot` 实时推送截图的逻辑已保留（`channel_images.py`），但本版平台浏览器工具尚未接入 SDK，因此没有调用方；接入后启用。
- iLink 协议实现参考 Nous Research 的 [hermes-agent](https://github.com/NousResearch/hermes-agent) Weixin 适配器（MIT），许可声明见 `THIRD_PARTY_NOTICES.md`。

接口：`GET/POST /api/channels`、`PUT/DELETE /api/channels/{id}`、`GET /api/channels/{id}/conversations`、`POST /api/channels/{id}/wechat/login`、`GET /api/channels/{id}/wechat/login/status`、`POST /api/channels/{id}/wechat/relogin`。

## 定时任务（Cron）

侧栏「Agent 能力 → 定时」按当前 Agent 管理定时任务。

- 新建任务时填写名称、任务内容（每轮发给 Agent 的消息）、5 字段 cron 表达式（分 时 日 月 周）、IANA 时区和是否启用；表达式非法、无法计算下一次触发、名称或内容为空、同一 Agent 重名都会被拒绝并显示中文错误。
- 每个任务保存一个 UUID 作为专属 SDK 会话；每次触发在同一 transcript 上入队一个 turn，用户在该会话的追问共享上下文。transcript 不存在时（会话被删除）下次触发重新生成 session id。
- `CronRuntime` 在 API 进程内每 `CRON_TICK_SECONDS`（默认 20 秒）扫描到期任务，复用 `SessionRegistry` 与并发上限。调度器按单进程部署设计，不要为 API 启动多个 uvicorn worker，否则可能重复触发。
- 到点时如果任务会话已有排队/运行中的 turn，本次触发跳过并写 `audit_log`；「立即运行」遇到忙返回 409。
- 调度在提交 turn 前先提交数据库（消费本次计划）；提交失败写审计，不重试。服务停机跨过多个触发点后恢复，每个任务最多补跑一次，随后按表达式继续。
- 暂停或删除任务不会取消已经产生的 turn；删除任务保留会话与 transcript；暂停/归档 Agent 时不触发，恢复后从下一个匹配时间继续。
- 可选推送到微信：任务可以显式选择一个当前 Agent 的 active 微信渠道下已有 1:1 联系人（`GET /api/channels/{id}/conversations` 列出候选）；触发时与租约同一事务写 pending 投递，复用渠道分块、重试和补发。删除渠道会清空该渠道相关任务的推送目标；删除会话或联系人只移除绑定，任务保留目标但届时静默不推送。未配置目标则只留在网页会话里，群聊不支持，取消的 turn 不发送。
- 任务页显示下次触发、最近运行时间、上次跳过提示和「打开会话」入口，支持暂停/启用、编辑、立即运行和删除。
- 平台不向 Agent 提供定时任务工具；`cron-scheduler` 只是初始化技能，说明「定时」页面入口和定时触发会话的语义。

接口：`GET/POST /api/cron-jobs`、`PUT/DELETE /api/cron-jobs/{id}`、`POST /api/cron-jobs/{id}/run`；响应为任务对象（`last_run_at` / `last_skipped_at`，不含 `last_run_id` / `last_run_status`）。

## 知识库（坚果云同步）

平台在生产机维护一份本地镜像（默认 `~/.super-personal-platform/knowledge/`），由 `KnowledgeSyncRuntime` 在 API 进程内与坚果云 WebDAV 双向同步：服务启动后立即同步一次（已配置时），之后按配置页的间隔增量同步（默认 300 秒）。只传输新增或变化的文件，不监听文件系统。

- 配置在「配置」页的「知识库」tab：启用开关、WebDAV 地址（例如 `https://dav.jianguoyun.com/dav/`）、账号、应用密码（坚果云「第三方应用密码」，不是登录密码）、根目录、同步间隔。保存后立即唤醒同步，不需要重启服务；密码只写入 `settings.json`，接口只返回 `has_password`。`.env` 的 `KB_WEBDAV_*` 只在还没有配置时作为首次种子。
- 同步规则：远端和本地都改过同一文件时，远端版本保留原名，本地版本另存为 `<name>.conflict-local-<时间戳>` 并上传；远端删除且本地未修改时删除本地；本地删除不传播，远端文件会在下一轮恢复。单文件默认上限 50 MB（`KB_MAX_FILE_BYTES`），超出跳过；路径限制在配置的根目录内，拒绝 `..` 和符号链接。
- 坚果云限流：免费账号每 30 分钟约 600 次 WebDAV 请求（所有第三方应用共享），单次 `PROPFIND` 最多 750 项，且不支持 `Depth: infinity`。平台默认每 4.5 秒最多发一个请求（`KB_REQUEST_INTERVAL_SECONDS`），默认跳过 `.obsidian`、`.git`、`.trash`、`node_modules`、`.DS_Store`（`KB_EXCLUDE_DIRS`）；收到 `BlockedTemporarily` 时暂停约 30 分钟（`KB_BLOCKED_RETRY_SECONDS`）。列表达到 750 项、被截断或某个子目录读取失败时，本轮不做删除判断。
- 更改 WebDAV 地址、账号或根目录时，本地镜像和同步状态会移到 `.old-<时间戳>` 备份后按新配置重建，避免旧文件被上传到新根目录。
- 本版知识库镜像不挂载给 Agent（知识库工具未接入）；页面配置与同步正常运行。
- 接口：`GET/PUT /api/knowledge` 读取和保存配置（响应包含同步状态，不含密码），`POST /api/knowledge/sync` 立即触发一次同步；`GET /api/system` 的 `knowledge` 字段也返回同步状态。

## 量化回测

顶部「功能」tab 的「回测」是独立页面，不绑定 Agent；首版只做加密货币现货、单标的、历史回测，不接实时行情与实盘交易。

- 行情：Bitget 公共 REST（无需 API Key），按需抓取并增量缓存到 `data/market.db`。周期支持 `1min/3min/5min/15min/30min/1h/4h/6h/12h/1day/1week/1M`；日/周/月使用 Bitget 的 UTC 变体（`1Dutc/1Wutc/1Mutc`）对齐，回测只使用已完成的 K 线。同一交易对和周期第二次回测只抓取缺失的首尾区间；每次回测还会扫描区间内的中间缺口并尝试补齐（`MARKET_GAP_REPAIR_BARS` 默认 50000 根、`MARKET_GAP_REPAIR_RANGES` 默认 50 段）。
- 网络：`api.bitget.com` 在部分网络无法直连，需要在 `~/.super-personal-platform/.env` 设置统一的 `HTTP_PROXY`/`HTTPS_PROXY`（生产机为 `http://192.168.1.2:7893`）。这些变量会作用于所有出站 HTTP 调用（模型、微信、行情）。页面「测试连接」或 `GET /api/market/probe` 可诊断代理与交易对。
- 策略：在页面内编写 Python，接口为 `def strategy(df) -> pd.Series`。`df` 是 UTC `DatetimeIndex`，列为 `open/high/low/close/volume`；返回每根 K 线的目标仓位 `[-1, 1]`（负数为模拟做空）。策略写入临时文件后在独立子进程中执行，默认超时 120 秒、内存上限 2 GB；这是崩溃隔离，不是安全沙箱。
- 数据完整性：页面「预热 K 线」默认 200 根（0–5000），会在评估区间之前额外加载，策略可在预热段建立初始仓位，指标只从开始日期计算。补不齐的缺口会以 NaN 行保留在策略输入中，缺口期间强制空仓、跨缺口收益不计入。平台还会把策略在截断前缀上重跑并比较重叠信号，发现「疑似使用未来数据」时在结果里告警；该检测会让策略代码再执行一次，策略应保持无副作用。可用 `BACKTEST_FUTURE_CHECK=false` 关闭该检测。
- 结果：总收益、年化、最大回撤、Sharpe、Sortino、胜率、交易次数、盈亏比、基准买入持有；价格 K 线与买卖点、净值、回撤、交易明细。净值/回撤/价格曲线下采样到约 2000 点，交易明细最多保存 5000 笔。结果持久化在 `backtests` 表，可在历史列表回看。
- 任务：`queued/fetching/running/completed/failed/cancelled`；服务重启后未完成任务重新排队；同时只执行一个回测，其余排队。
- 限制：单次回测最多 `BACKTEST_MAX_BARS`（默认 200000）根 K 线；缺口超过修复预算时保留为空仓并在结果里告警；模拟做空不含借币成本、资金费率与强平。
- Agent 可使用 `quant-backtest` 初始化技能；`backtest_*` / `market_*` 工具已通过进程内 `platform_tools` MCP 接入。

## 交易记录

「交易记录」在功能工作区有独立页面。记录保存在 `data/trade-journal.jsonl`，一行一笔，便于查看、复制和备份；不新增数据库表。页面支持状态/市场/关键词筛选、新建/编辑、平仓和统计；不提供删除、图表、附件或回测导入。

- 字段：`symbol`、`direction`、`entry_at`、`entry_price`、`thesis` 必填；`exit_at`、`exit_price` 平仓时同时提供；可选 `market`、`currency`、`instrument_type`、`stop_loss`、`quantity`、`fees`、`review`、`tags`、`followed_plan`、`meta`。
- 市场：`crypto` 默认 `USDT`/`spot`，代码如 `BTCUSDT`；`a_share` 默认 `CNY`/`stock`，代码建议 `600519.SH`、`000001.SZ`。`market` 可省略，平台会从代码推断。
- 计算：`status`、价格收益、R 倍数、净盈亏、胜负由平台计算，不写入文件；没有 `quantity` 时只有价格收益和 R，没有金额盈亏。
- 写入：服务层按进程内事件循环串行写入，更新用临时文件原子替换，文件权限 0600；文件损坏行会被跳过。
- 接口：`GET/POST /api/trade-journal`、`GET /api/trade-journal/stats`、`GET/PUT /api/trade-journal/{id}`、`POST /api/trade-journal/{id}/close`。
- Agent 可使用 `trade-journal` 初始化技能；五个 `trade_journal_*` 工具已通过进程内 `platform_tools` MCP 接入。
- 边界：不接交易所、不自动下单、不计算实时浮盈、不删除记录。

## 资产配置

功能工作区「交易」组提供独立的长期组合账本，不绑定 Agent，也**不读取交易记录**。它记录长期资金、持仓、目标比例和再平衡纪律，与短线交易记录完全分开。

- 组合：可建多个命名组合，每个组合一个基础币种（USDT、CNY……）；跨组合在顶部「总览」按 USD 折算合计（USD 与 USDT 按 1:1，CNY 按自动抓取的 USD/CNY 汇率）。
- 流水：入金、出金、初始持仓、买入、卖出、分红、费用；一行一笔存在 `data/allocation-ledger.jsonl`。`初始持仓` 不改变现金，用于录入已有仓位；初始现金另记入金。
- 推导：按时间重放流水，现金、数量、移动加权成本、已实现/未实现盈亏、投入本金、总收益和 XIRR 都由流水算出；卖出超过当时持仓会被拒绝；现金可以为负并提示。
- 目标：每个组合可设现金目标、标的目标占比和再平衡带宽（默认 5 个百分点）；目标合计不等于 100 只提示。页面显示实际 vs 目标、偏离和再平衡金额建议。
- 价格：`GET /api/allocation` 只读配置、流水、手动价和 `market.db` 缓存；点「刷新全部账本」才抓所有组合的 Bitget 1min 最新价，打开页面时也会自动刷新一次（同一浏览器会话 60 秒内不重复请求，失败保留旧价格并提示）。刷新同时抓 USD/CNY 汇率（东方财富 `133.USDCNH`，失败用 Yahoo `CNY=X` 兜底），成功后保存到 `allocation.json`，失败沿用上次值。优先级：实时 > 手动价 > 缓存 > 成本价；裸 ticker（如 `ETH`）会映射为 `ETHUSDT` 再请求；美股 ticker（如 `SPY`）先走东方财富 push2（105/106/107），失败再用 Yahoo Finance chart v8 兜底（Yahoo 需要代理，USD 计价，USDT 组合近似 1:1）；6 位 A 股基金代码（如 `000307`）通过东方财富基金接口抓最新公布净值，A 股股票按手动价。A 股股票、手动价和利息不会自动更新。
- 固定收益：在组合配置里给标的设置年化利率、起息日和是否复投，每次读取按天累计到持仓市值和浮盈（单利或按年复利）；配置了计息的标的刷新时不再抓行情。
- 总览：顶部「总览」把所有组合折算成 USD 合并：显示总资产、现金、投入本金、浮盈、已实现、总收益、总收益率和近似 XIRR（历史现金流按当前汇率折算），以及合并持仓明细（同一标的跨组合合并）。不支持的币种不计入并提示。
- 页面：功能 → 资产配置；不分 tab。上面是总览和合并持仓，中间是所有组合合并的流水（可按组合/标的筛选），最下面是组合设置（目标、再平衡、编辑/记一笔入口）。
- 接口：`GET /api/allocation`、`POST /api/allocation/refresh`、`POST/PUT/DELETE /api/allocation/portfolios...`、`GET/POST /api/allocation/portfolios/{id}/entries`、`PUT/DELETE /api/allocation/portfolios/{id}/entries/{entry_id}`。
- Agent 可使用 `allocation` 初始化技能；六个 `allocation_*` 工具已通过进程内 `platform_tools` MCP 接入。
- 边界：不接券商/交易所、不自动下单、不自动再平衡；没有每日估值快照，不做净值曲线、最大回撤和收益归因；总览的汇率和 XIRR 为近似值；不做税务批次、做空、保证金或借币成本。

## 缠论图

功能工作区「交易」组提供只读的缠论结构图，不绑定 Agent，也不产生交易信号。

- 数据：复用 Bitget 公共 REST 和 `market.db` 增量缓存，只使用已收盘 K 线；按需刷新，不引入 WebSocket。
- 范围：crypto 现货、`15min/1h/4h/1day/1week`；分析范围用 UTC 时间窗口（精确到所选周期、结束时间包含，默认最近 500 根对应窗口，20–1000 根）。
- 算法：确定性程序计算，不调用 AI。包含处理（向上取高高、向下取低低）、严格三根分型、老笔（顶底交替且至少 5 根合并 K 线含两端）、完整中枢（进入段 + 连续三笔重叠 + 确认离开段）、笔级别 1/2/3 买卖点（带失效标记）、MACD(12,26,9) 与简化背离。笔一旦成立不会因后续价格越过起点而回滚；未成笔分型不标注；最后一笔之后的未确认走势用虚线画出，不计入笔数、不参与中枢/背离/买卖点。买卖点和背离是笔级别近似，不是严格缠论背驰，也不是交易建议。
- 接口：`POST /api/chan/analyze` 返回 candles/merged/fractals/strokes/pending_strokes/zones/points/macd/divergences/rules/warnings，不建表、不落库；`POST /api/chan/backtest` 用因果前缀回放、下一根开盘进场、固定止损/R 目标/时间止损和费用，输出胜率、平均 R、回撤和按信号类型统计。
- 页面：交易 → 缠论图；顶部工具栏直接显示参数和显示项；中枢用半透明色块，买卖点标为 1买/2买/3买、1卖/2卖/3卖；MACD 副图与主图时间轴、十字线同步，背离标为「底背 / 顶背」。交易对、周期、时间窗口、结构/回测模式和各显示开关写入 `/chan?...` 查询串，刷新或直达后恢复并自动分析。
- Agent 可使用 `chanlun` 初始化技能；`chan_analyze` / `chan_backtest` 已通过进程内 `platform_tools` MCP 接入。

## 数据存储

生产数据都在 `~/.super-personal-platform/data/`：

- `settings.json`：平台配置（0600）：`runtime`（会话保留天数、旧配置迁移标记）、`knowledge`（WebDAV 配置，含密码）、`agents`（instructions、Provider、状态）、`channels`（iLink 凭据与同步游标）、`channel_conversations`（`"{channel_id}:{external_id}"` → session_id、display_name、context_token）。写前保留 `.bak`，主文件损坏时回退。
- `app.db`：SQLite（WAL，busy_timeout=5000）。新库只创建六张表；从旧版升级的库会保留未使用的旧表：
  - `audit_log`：审计流水（定时跳过、触发失败等）；
  - `cron_jobs`：定时任务定义、`session_id`（UUID）、`next_run_at`、最近运行/跳过、推送目标；
  - `channel_inbounds`：入站幂等键（渠道 + 外部消息 id），同时保存入站文本/联系人供引用找回；
  - `channel_deliveries`：出站投递义务与重试（pending/attempting/sent/failed）；
  - `backtests`：回测任务与结果；
  - `running_sessions`：运行时租约（`session_id` 主键）。
- `market.db`：Bitget K 线增量缓存（WAL），表 `market_candles` 和 `market_sync_state`；可以独立删除，下次回测会重新抓取。
- `trade-journal.jsonl`、`allocation.json`、`allocation-ledger.jsonl`：交易记录与资产配置数据，权限 0600，不建数据库表。
- `browser_state.json`：浏览器 cookies/localStorage（0600）；平台浏览器工具接入后用于跨会话、跨轮次保持登录态。
- `knowledge_sync_state.json`：知识库同步状态（etag + 本地哈希）。
- `backtests/`：回测策略临时文件。
- `~/.super-personal-platform/claude/`：SDK 配置、用户画像 `CLAUDE.md`、会话 transcript 与 auto memory；权限 0700。SDK transcript 是会话历史的唯一来源。
- `~/.super-personal-platform/workspaces/`：每个 Agent 的固定工作区；归档 Agent 不会删除这里的文件，彻底删除会连同工作区目录一起删除。
- `~/.super-personal-platform/claude/skills/`：全局技能库（Claude Code 用户级技能目录）。
- `~/.super-personal-platform/claude/plugins/`：官方插件集市与已安装插件的缓存；启用状态在 `claude/settings.json` 的 `enabledPlugins`，由插件页调用 Claude Code CLI 维护。
- `~/.super-personal-platform/knowledge/`：坚果云知识库的本地镜像。

备份时停服务后复制 `data/app.db`、`data/settings.json`、`claude/`、`workspaces/`、`skills/`，以及 `data/trade-journal.jsonl`、`data/allocation.json`、`data/allocation-ledger.jsonl`；`data/market.db` 可按需重建；`knowledge/` 是镜像，可不备份。删除会话会同时删除其 transcript、截图、投递记录与渠道绑定。

## 配置

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_HOME` | 配置与数据根目录 | `~/.super-personal-platform` |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `APP_DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite+aiosqlite:///$APP_HOME/data/app.db` |
| `APP_DATA_DIR` | 数据目录 | `$APP_HOME/data` |
| `APP_WORKSPACES_DIR` | 工作区根目录 | `$APP_HOME/workspaces` |
| `APP_KNOWLEDGE_DIR` | 知识库本地镜像目录 | `$APP_HOME/knowledge` |
| `LLM_BASE_URL` | 首次种子：Provider 端点（自动补 `/anthropic`） | `https://api.deepseek.com` |
| `LLM_API_KEY` | 首次种子：模型密钥 | 空 |
| `LLM_MODEL` | 首次种子：模型名 | `deepseek-v4-flash` |
| `CLAUDE_PERMISSION_MODE` | SDK 权限模式（本版 `bypassPermissions`，不是安全边界） | `bypassPermissions` |
| `RUN_TIMEOUT_SECONDS` | 单个 turn 的总超时 | `900` |
| `CLAUDE_CLIENT_TTL_SECONDS` | 缓存 SDK client 的空闲 TTL（秒） | `300` |
| `CLAUDE_CLIENT_MAX_CLIENTS` | 缓存 SDK client 上限（LRU 淘汰空闲项） | `8` |
| `CLAUDE_CLIENT_SWEEP_SECONDS` | 空闲 client 清扫间隔（秒） | `60` |
| `CLAUDE_CONNECT_TIMEOUT_SECONDS` | 连接 SDK CLI 的超时（秒） | `60` |
| `BROWSER_CONCURRENCY` | 跨 session 并发上限（同时是浏览器标签页上限，供平台浏览器工具使用） | `5` |
| `SESSION_RETENTION_DAYS` | 首次种子：会话保留天数；之后由配置页管理，0 表示不清理（写成 Claude 的 `cleanupPeriodDays`） | `30` |
| `CRON_TICK_SECONDS` | 定时任务扫描间隔（秒） | `20` |
| `CRON_DEFAULT_TIMEZONE` | 新建定时任务的默认 IANA 时区 | `Asia/Shanghai` |
| `CHANNEL_REPLY_MAX_CHARS` | 微信回复单块字符上限 | `1800` |
| `CHANNEL_DELIVERY_MAX_ATTEMPTS` | 单次发送与补发的尝试上限 | `3` |
| `CHANNEL_INBOUND_MAX_CHARS` | 单条微信入站文本上限 | `8000` |
| `CHANNEL_INBOUND_BATCH_SECONDS` | 同一联系人连续消息的打包窗口（秒，0 关闭） | `5` |
| `CHANNEL_INBOUND_BATCH_MAX_SECONDS` | 连续消息不断刷新时的最长打包时间（秒） | `15` |
| `CHANNEL_MEDIA_MAX_BYTES` | 单个微信图片/文件下载上限 | `20971520` |
| `CHANNEL_MEDIA_MAX_ITEMS` | 单条微信消息最多下载的媒体数 | `4` |
| `VISION_MAX_IMAGE_EDGE` | 图片附件压缩后的最长边（像素） | `1600` |
| `WEIXIN_LONG_POLL_TIMEOUT_MS` | iLink `getupdates` 长轮询窗口 | `35000` |
| `WEIXIN_API_TIMEOUT_MS` | iLink `sendmessage` 请求超时 | `15000` |
| `WEIXIN_QR_TIMEOUT_MS` | iLink 二维码请求/状态轮询超时 | `35000` |
| `KB_WEBDAV_*` | 知识库首次种子（URL/账号/密码/根目录）；之后由配置页管理 | 空 |
| `KB_SYNC_INTERVAL_SECONDS` | 知识库首次同步间隔（秒，最小 30） | `300` |
| `KB_SYNC_TIMEOUT_SECONDS` | 单次 WebDAV 请求超时（秒） | `30` |
| `KB_MAX_FILE_BYTES` | 单个知识库文件大小上限 | `52428800` |
| `KB_REQUEST_INTERVAL_SECONDS` | WebDAV 请求最小间隔（秒） | `4.5` |
| `KB_EXCLUDE_DIRS` | 同步时跳过的目录/文件名（逗号分隔） | `.obsidian,.git,.trash,node_modules,.DS_Store` |
| `KB_MAX_LISTING_ENTRIES` | 单次 PROPFIND 条目上限 | `750` |
| `KB_BLOCKED_RETRY_SECONDS` | 收到限流后的暂停秒数 | `1800` |
| `BITGET_BASE_URL` | Bitget 公共 REST 地址 | `https://api.bitget.com` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 统一出站 HTTP 代理，LLM、微信和行情共用；由 `settings` 导出到进程环境 | 空 |
| `NO_PROXY` | 不走代理的主机列表 | 空 |
| `MARKET_HTTP_TIMEOUT_SECONDS` | 单次行情请求超时 | `15` |
| `MARKET_RATE_LIMIT_PER_SECOND` | 行情请求限速（次/秒） | `5` |
| `MARKET_MAX_RETRIES` | 429/5xx/网络错误重试次数 | `3` |
| `MARKET_GAP_REPAIR_BARS` / `MARKET_GAP_REPAIR_RANGES` | 回测缺口修复预算 | `50000` / `50` |
| `BACKTEST_CONCURRENCY` | 同时执行的回测数 | `1` |
| `BACKTEST_TIMEOUT_SECONDS` | 策略子进程超时 | `120` |
| `BACKTEST_FETCH_TIMEOUT_SECONDS` | 单次回测抓取行情的总超时 | `600` |
| `BACKTEST_TOOL_WAIT_SECONDS` | Agent 的 `backtest_run` 工具最多等待秒数 | `90` |
| `BACKTEST_MEMORY_LIMIT_MB` | 策略子进程内存上限（POSIX best-effort） | `2048` |
| `BACKTEST_MAX_BARS` | 单次回测的 K 线上限 | `200000` |
| `BACKTEST_MAX_TRADES` | 结果中保留的交易明细上限 | `5000` |
| `BACKTEST_EQUITY_POINTS` | 净值/回撤/价格曲线下采样点数 | `2000` |
| `BACKTEST_FUTURE_CHECK` | 是否在截断前缀上重跑策略并检测未来函数 | `true` |

## 前端构建产物

`apps/web/dist` 提交到仓库。修改前端后先构建再提交；`package.json` 变化时先 `npm install`：

```bash
cd apps/web && npm install && npm run build
```

生产不构建 Web 镜像：FastAPI 直接托管已提交的 `apps/web/dist`（含 SPA fallback），API 和页面在同一个进程里。

## V1 生产部署（宿主机 systemd + SQLite）

生产机：`qiuqiu@192.168.1.3`，代码目录 `~/SuperPersonalPlatform-v1-deploy`，监听 8888。代码从 GitHub `main` 拉取；前端由 FastAPI 直接托管，配置和数据在 `~/.super-personal-platform/`。systemd unit 设置 `TimeoutStopSec=60`，用于覆盖停机时逐个缓存 client 的限时断开；不要在 unit 里改回 20 秒，否则 CLI 子进程可能被 SIGKILL。

### 首次准备

```bash
cd ~/SuperPersonalPlatform-v1-deploy
python3.12 -m venv .venv
.venv/bin/pip install -e apps/api
mkdir -p ~/.super-personal-platform/data
cp deploy/.env.example ~/.super-personal-platform/.env
# 编辑 ~/.super-personal-platform/.env，填 APP_AUTH_TOKEN、LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
# 外网访问需要代理时，设置 HTTP_PROXY 和 HTTPS_PROXY（生产机可用 http://192.168.1.2:7893）
# 知识库优先在配置页的「知识库」tab 配置；KB_* 仅作为首次启动的种子
cp deploy/agent-command-center.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

打开页面登录后，在「配置 → Provider」填写或确认 DeepSeek Anthropic 端点（`https://api.deepseek.com/anthropic`）、model 和 API key，然后新建会话开始聊天。

### 从旧版本升级（deepagents/LangGraph 版本）

先备份，再升级代码并重启：

```bash
systemctl --user stop agent-command-center.service
cp ~/.super-personal-platform/data/app.db ~/.super-personal-platform/data/app.db.bak
cp ~/.super-personal-platform/data/settings.json ~/.super-personal-platform/data/settings.json.bak 2>/dev/null || true
cd ~/SuperPersonalPlatform-v1-deploy
git pull --ff-only origin main
.venv/bin/pip install -e apps/api
systemctl --user start agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

升级后的行为与损失范围：

- 本版**不做数据迁移**：旧库里的自定义 Agent 名称/instructions、Provider 配置、知识库配置、旧用户记忆和旧聊天历史都不会导入。`data/settings.json` 用环境变量和默认值重新种子（仅首次启动读取旧 `app_configs.session_retention_days` 一次），之后在配置页重新填写 Provider、Agent 与知识库。
- 旧 `app.db` 可以直接沿用。启动时会检测旧结构的 `channel_deliveries`、`channel_inbounds` 并重建：旧待投递和入站去重记录会丢失，iLink 可能重放一次已处理的消息。
- 旧 `providers`/`agents`/`channels`/`sessions`/`messages`/`runs`/`run_events`/`memory_items` 等表会留在库里但不再读写；`app_configs` 仅在首次启动读取一次保留天数后不再使用；可以在备份后手动删除旧表。
- 旧版本把微信渠道凭据和联系人绑定存在 `app.db`，它们不会迁移到 `settings.json`，升级后需要在「渠道」页重新扫码绑定。
- 旧定时任务如果 `cron_jobs` 列结构与新模型不兼容，ORM 查询会因缺列失败：定时 tick 持续记录 `cron tick failed`，任务列表接口也可能 500。先备份数据库，再决定删除重建（任务配置会丢失）。
- 回测历史保留（`backtests` 表结构兼容）。
- 旧 home 里遗留的已退役技能（如 `memory-organizer`、`knowledge-base`）不再由 seed 删除：模板移除时只删 `.seed_state.json` 记录，本地文件保留，由用户自行清理。

如果选择彻底重建 `data/app.db`：停止服务，删除 `~/.super-personal-platform/data/app.db` 后重启即可。会丢失回测历史、定时任务、审计记录、未完成投递与入站去重记录；`data/settings.json`（含渠道配置与联系人绑定）和会话 transcript 不受影响，微信不需要因此重新扫码。

### 日常更新

```bash
cd ~/SuperPersonalPlatform-v1-deploy
git pull --ff-only origin main
# 只有 pyproject 依赖变化时才需要重新安装
.venv/bin/pip install -e apps/api
systemctl --user restart agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

`deploy/agent-command-center.service` 使用 `~/.super-personal-platform/.env` 作为 `EnvironmentFile`，并强制 `APP_DATA_DIR=%h/.super-personal-platform/data`、`APP_DATABASE_URL=sqlite+aiosqlite:///%h/.super-personal-platform/data/app.db`。用户级服务依赖 linger（`loginctl show-user qiuqiu -p Linger` 应为 `yes`），否则退出登录后进程会被回收。

回滚 v1 版本：checkout 上一个已验证的提交，重新安装依赖（如需要）并重启：

```bash
cd ~/SuperPersonalPlatform-v1-deploy && git checkout <commit> && systemctl --user restart agent-command-center.service
```

不要回退 SQLite 数据文件；旧版本通常能读取被新版本保留的旧表，但会话数据已迁移到 SDK transcript，回滚会看不到新会话。

## 测试

```bash
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

需求见 `docs/intent.md`；协作约定见 `CLAUDE.md`；迁移设计见 `docs/superpowers/specs/2026-11-10-claude-agent-sdk-migration-design.md`。
