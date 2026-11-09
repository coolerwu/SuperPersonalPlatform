# Agent Command Center

v1：网页对话助手（Chat + 多 Agent + Subagent + Checkpoint/Runs + 配置页）、多渠道（微信个人号）与定时任务（cron），浅色界面，assistant 消息支持 Markdown。

- `apps/api`：FastAPI + deepagents + LangGraph
- `apps/web`：React + Vite + TypeScript
- `deploy/`：systemd unit 与 `.env` 模板（生产）
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888

## 运行时目录

配置与数据统一在 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：

```text
~/.super-personal-platform/
├── .env                 # APP_AUTH_TOKEN、LLM_* 等配置
├── data/
│   ├── app.db           # SQLite
│   ├── checkpoints/     # 每个 session 的 LangGraph checkpoint
│   ├── trade-journal.jsonl  # 交易记录（一行一笔，0600）
│   ├── allocation.json  # 长期组合配置（目标/现金/手动价，0600）
│   ├── allocation-ledger.jsonl  # 长期组合流水（一行一笔，0600）
│   ├── browser_state.json  # 浏览器 cookies/localStorage（0600，可删）
│   └── knowledge_sync_state.json  # 知识库同步状态（etag / 本地哈希）
├── workspaces/          # Agent 真实工作区根目录
├── skills/              # 全局技能库（每个技能一个目录；.seed_state.json 记录内置技能哈希）
└── knowledge/           # 坚果云知识库的本地镜像（启用技能后挂到 /knowledge/）
```

启动目录下的 `.env` 会覆盖 home 配置，便于本地临时覆盖。

## 本地开发

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e "apps/api[dev]"
.venv/bin/playwright install chromium   # 下载慢时可加 PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

cd apps/api
../../.venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
cd apps/web
npm install
npm run dev
```

打开 `http://localhost:5173`，默认 token 见 `APP_AUTH_TOKEN`（默认 `dev-token`）。

本地聊天需要有效模型密钥：通过 `export LLM_API_KEY=...`、`~/.super-personal-platform/.env` 或启动目录 `.env` 配置；不要提交。也可以登录后在侧栏「配置」里填写。

## Chat

- 默认 Assistant 的 instructions 为中文，参考 Hermes Agent 的默认身份 behavior spec；
- 新对话采用懒创建：点击「新对话」只清空当前视图，首次发送时才创建 Session；
- 每个页面有独立路径：`/chat`（打开某个会话为 `/chat/<session_id>`）、`/config`、`/memory`、`/skills`、`/tools`、`/channels`、`/cron`、`/backtest`、`/journal`、`/allocation`、`/chan`；刷新或直接打开链接会回到对应页面和会话，浏览器前进/后退可用，未知路径回到聊天页；选中的 Agent 会记在浏览器本地，下次打开沿用；
- 对话里以“记住：……”开头的内容会写入用户记忆；
- 用户消息和 assistant 消息都按 Markdown 渲染：GFM 表格/列表、代码高亮、代码块复制、链接、图片，以及 `$$...$$` 数学公式（KaTeX；单 `$` 仍按普通文本处理，避免和金额混淆）；
- 消息操作：复制、引用；按钮位于气泡下方、气泡之外，引用内容显示在输入框上方并可单独取消，发送时以 `> ` 引用块写入消息；失败 Run 可重试；
- 流式输出、停止生成、运行中排队追问、自动增高输入框；Run 运行中停止键和发送键并排显示，发送的追问进入同会话队列并显示「已排队，前面的任务完成后会自动执行」；运行状态按会话保存，切走再切回会继续显示该会话的流式文本、执行过程和截图；发送时强制滚到底部，输出期间上滑后显示浮动的「回到最新」按钮；模型返回 `reasoning_content` 时，聊天中显示可折叠的「执行过程」：折叠条左对齐并占满消息宽度，连续思考片段与工具调用按发生顺序交错展示，思考/工具阶段展开，开始输出答案后自动折叠，完成后默认折叠、可展开；流式文本约 80ms 节流后渲染 Markdown，长回答和思考过程不会每个 token 重排一次；
- 多个会话可以各自跟踪自己的 Run：切走 A 去加载 B 时先清空旧消息并显示骨架，B 不会出现 A 的文本；另一个会话的 Run 运行中时当前会话仍可发送，Run 按并发上限立即运行或排队，不再禁用输入区；异步错误只显示在所属 Agent/会话。失败或取消的 Run 会保留已流出的内容，显示「失败」/「已停止」气泡、错误摘要与重试按钮，刷新后从 `run_events` 恢复；失败和 API 错误按认证、限流、上下文超限、超时、连接、配置缺失等类别显示中文提示，原始错误仍可在气泡标题或抽屉中查看；
- 草稿、附件和引用按会话隔离：切换会话会恢复各自的草稿、附件和引用，引用在发送成功后清除，发送失败时写回发起发送的会话；「新对话」使用独立的未发送草稿；「整理记忆」不会消费草稿、附件或引用，新建会话时会把草稿和附件带过去；
- 页面可见时每 5 秒刷新当前 Agent 的会话列表：微信入站产生的新会话、消息数和运行中 Run 会更新侧栏徽标；当前会话有活动 Run 时会自动接上它的实时进度；微信会话在侧栏和输入区都有提示；会话按最后一条消息时间倒序排列，Run 完成不会把会话顶上去；
- 侧栏搜索：会话列表上方的搜索框按关键词检索当前 Agent 的历史消息（至少 2 个字符，300ms 防抖）；结果显示会话标题、片段、角色和时间，点击后切换到该会话并滚动到命中消息、短暂高亮；
- 后端同一 session 最多一个 running Run，queued Run 可以排队；网页 Chat 在运行中发送会创建 queued Run 并显示「已排队」，当前 Run 完成后按创建顺序自动执行；retry 在已有活动 Run 时仍返回 409（带 `active_run_id`），前端接入该 Run 并提示刚才的消息没有发送、恢复草稿；微信入站 5 秒打包后也进入同一队列，由 worker 串行执行；不同 session 的 Run 最多并发 `BROWSER_CONCURRENCY` 个（默认 5），超出的 Run 保持 `queued`；
- 服务停止/重启时正在运行的 Run 会重新排队并在启动后继续；用户主动取消的 Run 不会被重启复活；仍打开的页面会自动重连（瞬时连接错误按退避重试）；收到 `reason=service_restart` 的 `run_queued` 时清空上一次尝试的流式文本，并显示「服务重启，Run 已重新排队」。恢复时保留中断前的 checkpoint 消息并追加「继续上次任务」提示，不重复原始用户输入；未返回结果的工具调用会补一条结果未知的 error 消息。重新入队不会回滚已经发生的文件/浏览器副作用，恢复提示会要求 Agent 不要重复执行；
- 会话保留：最后一条消息（没有消息时按创建时间）超过配置页「运行环境」tab 设置的保留天数（默认 30 天，0 表示不清理）的会话会被后台任务自动删除，并同时清理消息、Run、Run 事件、checkpoint 和截图目录；有排队/运行中 Run 的会话跳过。微信会话解绑后，该联系人下一条消息会新建会话；定时任务在下次触发时重建会话。删除不可恢复；`SESSION_RETENTION_DAYS` 只在首次创建配置时作为默认值；
- 模型调用遇到 429/5xx/超时/连接错误时最多重试 3 次（指数退避 + jitter）；401/400 等 4xx 不重试；重试在模型层实现，主 Agent、general-purpose 子 Agent 和 step-limit 总结都会覆盖；流式调用已输出 token 后不重试，避免重复文本；可重试失败会记录 warning 日志；
- 单个 Run 的步骤上限默认 500（`RUN_RECURSION_LIMIT`，主 Agent 与子 Agent 共用）；真的达到上限时先在原会话上自动续跑一次，仍超限才用同一模型带上完整对话生成最终答复，Run 仍以 completed 收尾并标注被截断；
- Agent 可在当前工作区内查看、创建和修改文件（ls / read_file / write_file / edit_file / delete / glob / grep）；所有 Agent 都可以用 `execute` 在宿主机执行命令（LocalShellBackend，以服务账号权限运行，没有沙箱），用 `eval` 在隔离的 QuickJS 环境里执行 JavaScript（无文件与网络，PTC 只开放文件工具）；两者无需启用技能或读取授权。浏览器能力通过 `web-browser` 技能解锁（无头 Chromium，但默认使用普通桌面浏览器身份（真实 Chrome UA、zh-CN、隐藏 `navigator.webdriver`），登录态保存到 `data/browser_state.json` 并跨 Run 复用；默认返回文字和元素引用；用户明确要求或需要展示登录二维码时可返回页面截图，截图保存到 Agent 工作区并显示在对应回复下方）；
- 技能与工具：左侧「技能」页面为当前 Agent 启用技能；模型读取 `/skills/<name>/SKILL.md` 后，技能声明的工具才会解锁；上下文压缩后会自动重新注入已启用技能的正文，保持解锁；文件工具始终可用；「工具」页面展示平台工具、文件工具与技能工具的注册与声明情况；平台工具 `skill_save` 始终可用，Agent 可以在对话中主动把可复用流程创建成技能，AI 创建的技能会自动加入当前 Agent 的启用列表，下一次对话生效；
- 空状态提供建议提示词；详情抽屉默认关闭，关闭时内容列自动加宽。
- 消息历史分页：Chat 首屏只加载最新 50 条，向上滚动按 50 条加载更早消息并保持滚动位置；搜索命中会自动向前翻页定位并高亮。
- 顶部轻量栏显示当前 Agent 和会话标题，并作为详情抽屉开关。
- 响应式：桌面为侧栏 + 对话 + 详情三栏；窄屏（≤860px）用顶部栏和汉堡菜单打开侧栏抽屉，详情变为全屏面板，输入区适配底部安全区，移动端外壳保持整屏高度，键盘弹起时由浏览器平移可视视口露出 composer，收起后自动恢复。功能工作区（回测、交易记录、资产配置、缠论图）同样使用顶部栏和抽屉；页面头部描述占满整行、操作按钮换行右对齐，交易记录、回测与缠论回测的交易明细、资产配置的持仓与流水改为带字段标签的卡片列表。
- 输入卡片左侧「＋」菜单提供上传附件、整理记忆和清空输入：文件保存到当前 Agent 的 `uploads/` 工作区目录（单文件 10 MB，上传时显示单文件进度和大小），图片支持选择、粘贴、拖拽，大图在浏览器端缩放到最长边 1600 px 后上传；发送后消息里带上文件名、工作区路径和字节数，Agent 可用文件工具读取，图片还会作为图片输入发给模型（最多 4 张、单张 4 MB）；超过单张上限的图片会先用 Pillow 在服务端压缩（最长边 ≤ `VISION_MAX_IMAGE_EDGE`，默认 1600，JPEG 质量逐级降低），压缩结果只用于模型输入，工作区原图不变；仍无法压缩或无法识别的图片会在消息文本末尾追加系统提示，模型会告诉用户图片未加载、请压缩后重发，不再静默丢弃。聊天里图片显示缩略图、点击可看原图，其他附件显示为文件 chip；上传文件通过鉴权接口 `GET /api/agents/{agent_id}/uploads/{filename}` 读取；「整理记忆」显示为命令卡片（原始指令可展开），不再把内部文本直接铺开；「整理记忆」直接发起一个 Run 并触发 `memory-organizer` 技能；也可以清空输入。

## 配置页

侧栏底部「配置」进入配置页，顶部用 `Provider / Agent / 运行环境` 三个 tab 切换，不需要一路滚到底；Provider 默认只显示列表，点条目展开表单；Agent 列表点击行切换当前 Agent，点「编辑」展开表单。**配置页始终编辑当前 Agent（与侧边栏共用一个选择状态）**；Agent tab 列出全部 Agent 的摘要，点击任意一行即可切换当前 Agent，侧边栏选择器同步变化；编辑区只编辑当前 Agent。切换 Agent 时，Provider tab 会自动选中该 Agent 的 Provider；输入草稿按 Agent 分别保留（当前页面内），切换时旧 Agent 的会话和消息不会短暂显示在新 Agent 下。集中管理：

- Provider：查看全部 Provider、密钥是否已配置、新增和编辑；
- Agent：列表展示全部 Agent 的 Provider/model、启用技能数、自动记忆和状态，点击行切换当前 Agent；下方编辑名称、Provider、instructions、工作区，或新建 Agent；新建时未填写 instructions 会使用默认的中文个人助手提示词（能力说明 + 行为规范）；
- 工作区：每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/`，不支持自定义路径；配置页只读展示机器目录，并随左上角选中的 Agent 变化。Agent 可以查看和修改工作区内的文件；
- 技能：在侧边栏「Agent 能力」组的「技能」页面按当前 Agent 勾选启用；未启用的技能不会进入系统提示，读取它的 SKILL.md 也不会解锁工具。磁盘上已删除但仍在启用列表里的技能会显示为“未找到”，可取消勾选移除；
- Agent 彻底删除：输入 Agent 名称确认后，级联删除它的会话、消息、Run、事件和 checkpoint（有活动 Run 时拒绝）；
- 运行环境：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关（`GET /api/system`）；同时可设置会话自动清理的保留天数（默认 30 天，0 表示不清理）和自动记忆整理间隔（每 N 个用户回合，默认 10，0 表示关闭；`GET/PUT /api/settings`，存 `app_configs`）。

Run 事件存放在 `run_events` 表；前端默认折叠 `assistant_delta` / `reasoning_delta` 流式片段，在右侧抽屉里展开查看。`tool_call` 事件记录工具名与解析后的参数，聊天中的活动行据此显示「正在读取 …」「正在浏览 …」，运行超过 10 秒后附上已用时间。

## 技能与工具页面

侧栏顶部是「Agent / 功能」两个工作区 tab（默认 Agent）。Agent 工作区按「Agent 工作台 / Agent 能力 / 平台」分组：Agent 工作台放 Agent 选择器、新对话和会话列表；「Agent 能力」组提供技能、工具、渠道、记忆四个页面；「平台」组放配置和退出登录。功能工作区放不绑定 Agent 的手动工具（当前有回测、交易记录、资产配置、缠论图），不显示 Agent 选择器和会话。技能与工具页面的说明如下：

- **技能**：列出全局技能库，按「系统技能」和「交互创建」分组，按当前 Agent 勾选/取消启用，立即保存、下一轮 Run 生效；展示 description、来源徽标、声明的工具、SKILL.md 路径和解析警告；点「查看」可打开 SKILL.md 全文并直接编辑保存；非系统技能可删除（会删除技能目录并从所有 Agent 的启用列表移除）。新建技能可以让 Agent 在对话里用 `skill_save` 主动沉淀，也可以按你的要求创建；AI 创建的技能会自动加入当前 Agent 的启用列表。
- **工具**：展示平台工具（`skill_save`、`session_search`、`session_read`，始终可用）、文件工具（始终可用）和技能工具；技能工具显示是否已在平台注册、由哪些技能声明。启用声明它的技能并在对话中读取 SKILL.md 后解锁，没有单独的工具开关。

## Skills

全局技能库存放在 `~/.super-personal-platform/skills/`（`APP_SKILLS_DIR` 可覆盖），每个技能一个目录：

```text
skills/
└── <skill-name>/
    ├── SKILL.md
    └── 其他辅助文件（可选）
```

`SKILL.md` 必须有 YAML frontmatter，`name` 与目录名一致，`description` 用于系统提示里的技能列表：

```markdown
---
name: example-skill
description: 什么时候应该使用这个技能
metadata:
  include_tools: some_tool another_tool
---

# 技能正文
模型读取本文件后会看到这里的完整说明。
```

- `metadata.include_tools` 必须是**空格分隔的工具名**（不是 YAML 列表、不用逗号）；这些名字对应平台代码注册表 `apps/api/app/services/skill_tools.py` 里的工具。
- 未读取 `SKILL.md` 前，技能工具不会出现在模型工具列表里，调用会被当作未知工具；读取成功后，在同一会话内只要这段读取还在上下文中，工具就保持可用；上下文被摘要压缩掉后重新锁定。
- 每次 Run 都会重新扫描技能目录并重新应用 Agent 的启用列表，所以新增/编辑技能或修改启用状态在下一轮对话生效。
- 文件工具（ls / read_file / write_file / edit_file / delete / glob / grep）始终可用，不受技能开关影响。
- 技能来源：`.seed_state.json` 跟踪的系统内置技能标记为 `builtin`；Agent 用 `skill_save` 创建、frontmatter 带 `metadata.source: ai` 的技能标记为 `ai`；其他文件标记为 `manual`。技能页按「系统技能」和「交互创建」分组展示，AI 创建显示对应徽标。
- 技能创建：Agent 可以在对话里主动调用平台工具 `skill_save` 把可复用流程保存成新技能（通过 `skill_save` 每次 Run 最多 3 个，不会覆盖已有技能）；也可以按用户要求创建。也可以在主机上编辑文件。
- 技能启用：AI 创建的技能会自动加入当前 Agent 的启用列表，同一会话的下一次对话生效；手动创建/放入的技能仍需在这里启用。读取 SKILL.md 后，技能声明的工具才会解锁。
- 删除：非系统技能可以在技能页删除，删除整个技能目录并从所有 Agent 的启用列表移除；系统技能不能删除。
- 因为 Agent 可以写技能，技能声明本身不是安全边界；控制点是当前 Agent 启用了哪些技能，启用技能即允许它声明的已注册工具。
- 内置技能会在技能库中 seed：`web-fetch`、`web-search`、`web-browser`、`memory-organizer`、`quant-backtest`、`cron-scheduler`、`trade-journal`、`allocation`、`chanlun` 和 `knowledge-base`。`web-fetch` 声明 `include_tools: web_fetch`；`web-search` 声明 `web_search web_fetch`；`web-browser` 声明七个 `browser_*` 工具；`memory-organizer` 声明 `memory_list memory_update memory_delete`；`quant-backtest` 声明 `backtest_run backtest_status backtest_result backtest_cancel market_probe market_cache_status`；`cron-scheduler` 声明 `cron_list cron_targets cron_create cron_update cron_delete cron_run`；`trade-journal` 声明 `trade_journal_record trade_journal_update trade_journal_close trade_journal_query trade_journal_stats`；`allocation` 声明 `allocation_overview allocation_entries allocation_portfolio_create allocation_portfolio_update allocation_entry_record allocation_entry_update`；`chanlun` 声明 `chan_analyze chan_backtest`；`knowledge-base` 不声明工具，作用是把本地知识库镜像挂到 `/knowledge/` 并说明读写约定。写入内容的哈希记录在 `skills/.seed_state.json`：用户未修改的内置技能会随版本自动更新，改过的保持不动；没有记录的既有文件不会被覆盖，直到其内容与内置默认一致后开始跟踪。

## 工具

平台工具（始终可用，不参与技能门控）：

- **`skill_save`**：把可复用的工作流程保存成技能。参数 `name`（小写字母/数字/连字符）、`description`、`instructions`、`include_tools`（空格分隔的已注册工具名，可空）；写入 `/skills/<name>/SKILL.md` 并标记 `metadata.source: ai`；通过 `skill_save` 每次 Run 最多创建 3 个，不能覆盖已有技能。创建后自动加入当前 Agent 的启用列表，下一次对话生效。
- **`session_search`**：按关键词搜索当前 Agent 的历史会话消息，返回命中片段的 session_id、message_id、会话标题、角色和时间。关键词不要传整句问题。
- **`session_read`**：先用 `session_search` 拿到 session_id 和 message_id，再读取命中消息前后各 `before` / `after` 条；不传 message_id 时返回该会话最近的消息。只允许当前 Agent 的会话。

平台注册表当前提供以下技能工具：

- **`web_search`**：调用 DeepSeek Search API（`POST {provider.base_url}/v1/tools/search`），请求 `query`（1-128 字）、`count`（1-50，默认 10）、`need_content`，返回标题、链接、摘要、来源、日期、权威性评分。支持 `site:`、`-`、引号、`lang:`、`after:`、`before:` 等搜索语法。
- **`web_fetch`**：调用 DeepSeek Fetch API（`POST {provider.base_url}/v1/tools/fetch`），请求 `{url}`，返回 `{title, content}`（Markdown）。`url` 必须是完整 http/https 地址，不能带用户名/密码。
- **浏览器工具**（Playwright，本地无头 Chromium，普通桌面浏览器身份）：
  - `browser_open(url, tab="main")`：打开网页，返回标题、正文和可交互元素 ref；
  - `browser_snapshot(tab="main", wait_seconds=0)`：读取当前标签页的最新快照；`wait_seconds`（0-60）先等待再读取，用于等待扫码登录或异步渲染；扫码登录按 60 秒一段循环，最多等 5 分钟，二维码过期就重新截图；
  - `browser_click(ref, tab)`：点击快照中的元素；
  - `browser_fill(ref, text, tab, submit=false)`：填写输入框或 select；
  - `browser_press(key, tab)`：按下按键；
  - `browser_screenshot(tab, full_page=false)`：按需截取当前页面，返回 PNG 图片；用户要求查看截图、外观或布局，或需要用户扫码登录时调用，Provider 模型需要支持图片输入；PNG 同时保存到 `workspaces/<agent_id>/screenshots/<session_id>/<run_id>/`，聊天里显示在对应 assistant 消息下方，Agent 文件工具也能访问；
  - `browser_close(tab)`：关闭标签页并释放并发槽位。
  - 每次 Run 懒启动一个浏览器进程和一个 context，最多 `BROWSER_CONCURRENCY=5` 个标签页；Run 结束、失败、超时或取消时自动关闭，并在关闭前把 cookies/localStorage 写入 `data/browser_state.json`，下一次 Run 自动加载；ref 只对最近一次快照有效，动作后会返回新快照。
  - 只允许公开 http/https 地址，拒绝 localhost、内网/回环/链路本地地址、带凭据 URL、`file://` 和下载；只处理主 frame，不接管弹窗。配置了 `HTTPS_PROXY`/`HTTP_PROXY` 时 Chromium 显式走同一代理（`NO_PROXY` 作为 bypass），此时域名由代理解析，本地 DNS 污染不再阻断访问，但仍拦截 localhost 和私网地址字面量；未配置代理时保持原来的本地解析校验。
  - 搜索类页面优先尝试移动版/轻量入口（`m.baidu.com`、`www.google.com/search?q=`、`html.duckduckgo.com`、`cn.bing.com`、维基百科）；配置了代理时 Google、YouTube 等站点经由代理访问，未配置代理时仍需主机能直连；遇到验证码、安全验证或 HTTP 403/429 时返回明确错误并提示换入口，不会尝试绕过验证。

`web_search` 和 `web_fetch` 使用当前 Agent Provider 的 key，需要 Provider 支持 DeepSeek Search/Fetch；成功后按请求计费（各每 1000 次 5 元），分别受 DeepSeek QPS 限制（Search 100、Fetch 25）。使用条件：启用 `web-search` 并读取其 SKILL.md 后，会同时解锁 `web_search` 和 `web_fetch`；只抓取网页时也可以启用独立的 `web-fetch` 技能。`web-browser` 同样需要启用并读取其 SKILL.md。

- **`memory_list` / `memory_update` / `memory_delete`**：读取、更新、删除长期记忆（`memory-organizer` 技能声明）；列表返回 id、scope、标签和正文，更新/删除只能作用在 user memory 或当前 Agent 的 agent memory 上，其他 Agent 的记忆返回 not found；不提供新增工具。
- **`backtest_run` / `backtest_status` / `backtest_result` / `backtest_cancel` / `market_probe` / `market_cache_status`**：加密货币回测工具（`quant-backtest` 技能声明）。`backtest_run` 创建 Bitget 现货单标的回测（可指定 `warmup_bars`，默认 200）并最多等待 90 秒，完成返回指标摘要，未完成返回任务 id；`backtest_status` 查状态/进度；`backtest_result` 返回指标、告警和最近交易；`backtest_cancel` 取消排队或执行中的任务；`market_probe` / `market_cache_status` 检查 Bitget 连接和本地行情缓存。
- **`cron_list` / `cron_targets` / `cron_create` / `cron_update` / `cron_delete` / `cron_run`**：定时任务工具（`cron-scheduler` 技能声明）。只操作当前 Agent 的任务；`cron_list` 返回任务 id、表达式、时区、启停、下次触发、最近状态、专属会话和推送目标；`cron_targets` 列出当前 Agent 的微信渠道和已有 1:1 联系人会话；`cron_create` 创建任务并生成专属会话，prompt 必须自包含，可同时传 `push_channel_id` / `push_conversation_id`；`cron_update` 修改指定字段，两个推送 id 都传空字符串表示清除；`cron_delete` 删除配置但保留会话；`cron_run` 立即运行一次且不改变原计划。未启用技能或未读取 SKILL.md 时工具不可见。
- **`chan_analyze`**：缠论分析工具（`chanlun` 技能声明）。参数 `symbol`、`granularity`（`15min/1h/4h/1day/1week`）、时间窗口 `start_date` / `end_date`（UTC 日期，结束日包含在内，默认最近 500 根对应窗口，窗口 20–1000 根）；返回数据区间、合并 K 线、笔/未完成走势/中枢/1/2/3 买卖点、MACD 面积背离摘要和 warnings，不返回完整 K 线数组；买卖点带失效标记；结构是程序化老笔近似，不是交易建议。
- **`chan_backtest`**：缠论信号回测/交易计划工具（`chanlun` 技能声明）。因果前缀回放：信号收盘确认、下一根开盘进场；一次一笔，固定止损、R 倍目标、时间止损和单笔风险；参数含时间窗口 `start_date` / `end_date`（60–1000 根）、`stop_pct`、`risk_pct`、`r_multiple`、`max_hold_bars`、`fee_pct`、`slippage_pct`。返回收益、回撤、胜率、平均 R、按 1/2/3 买卖点分组和最近交易；样本少时明确提示。
- **`trade_journal_record` / `trade_journal_update` / `trade_journal_close` / `trade_journal_query` / `trade_journal_stats`**：交易记录工具（`trade-journal` 技能声明）。`trade_journal_record` 记录一笔 crypto 或 A 股交易（必填 symbol、entry_at、entry_price、thesis，平仓可同时给 exit_at/exit_price/review），`trade_journal_update` 修改已有字段，`trade_journal_close` 平仓并补复盘，`trade_journal_query` 按 id/市场/标的/状态/标签/时间/关键词查询，`trade_journal_stats` 输出胜率、平均收益、平均 R、盈亏比和按市场/标签分组。记录保存在 `data/trade-journal.jsonl`；工具不删除记录，也不接交易所。
- **`allocation_overview` / `allocation_entries` / `allocation_portfolio_create` / `allocation_portfolio_update` / `allocation_entry_record` / `allocation_entry_update`**：资产配置工具（`allocation` 技能声明）。`allocation_overview` 读取长期组合摘要、目标/偏离/再平衡、XIRR 和 warnings，`refresh=true` 才抓 Bitget 加密行情和东方财富/Yahoo 美股行情；`allocation_entries` 查看流水；创建/修改组合用 `allocation_portfolio_create` / `allocation_portfolio_update`；记录/修正流水用 `allocation_entry_record` / `allocation_entry_update`。工具不删除流水或组合，删除由页面处理；与交易记录完全分开。
- **`execute`**（`LocalShellBackend`，不在代码注册表）：所有 Agent 始终可用。命令通过系统 shell 以服务账号权限运行在工作区目录，但**不受工作区限制、没有沙箱**；子进程只注入 `PATH`、`HOME`、`LANG`，不继承平台密钥环境变量。命令默认 120 秒超时。任何 Agent（包括微信入站会话）都能操作这台机器。
- **`eval`**（`CodeInterpreterMiddleware`，不在代码注册表）：每次 Run 都挂载，在持久化的 QuickJS 上下文里执行 JavaScript，按会话隔离；默认 5 秒超时、64 MiB、每次最多 256 次 PTC 调用。环境没有文件系统、网络、`require` 或 `process`；通过 `tools.readFile` / `tools.writeFile` 等 PTC 接口调用文件工具。PTC 只开放文件工具，不开放 `execute`；`subagents=True`，可在 REPL 里用 `task(...)` 调度子 Agent（每次独立会话，不走父级审批）。general-purpose 子 Agent 不继承 `eval`，但可以使用 `execute`。

`execute` 与 `eval` 不参与技能启用列表，也没有读取门控；工具 schema 和库自带提示词负责用法说明，默认 Agent 的提示词会说明两者的存在与安全约束。

工具实现分别在 `apps/api/app/services/skill_tools.py`（Search/Fetch）、`apps/api/app/services/browser_tools.py`（浏览器）、`apps/api/app/services/memory_tools.py`（记忆）、`apps/api/app/services/backtest_tools.py`（回测/行情）、`apps/api/app/services/trade_journal_tools.py`（交易记录）和 `apps/api/app/services/chan_tools.py`（缠论）；`execute` 由 `deepagents` 的 `LocalShellBackend` 提供，`eval` 由 `langchain-quickjs` 提供。新增注册表工具时注册一个 factory，按运行时 Provider/会话构建 LangChain `BaseTool`。

## 记忆

侧栏「记忆」进入独立记忆页面：

- **用户记忆**：所有 Agent 共享，适合偏好、长期事实；
- **Agent 记忆**：只对当前 Agent 生效，适合角色和领域知识；
- 手动新增/编辑/删除/搜索；对话里“记住：……”会立即写入用户记忆；
- Agent 编辑区有「自动学习记忆」开关：开启后，Run 完成时最多返回 12 个记忆操作；先用 FTS5（trigram）在 user memory 和当前 Agent memory 中找出相关历史记忆，再让模型返回 add/update/delete 做合并，并为新增条目选择 `scope=user|agent`（用户长期偏好/个人事实写 user，Agent 工作方式/项目知识写 agent，不确定用 agent；update/delete 保持原 scope），去重后直接写入，每个 scope 上限 200 条；提取在 Run 完成后以后台任务执行，不占用 Run 并发槽，同一 Agent 的提取串行执行，避免并发写入重复记忆；
- **整理记忆**：聊天输入区「＋」菜单的「整理记忆」指令会直接发起一个 Run，读取 `memory-organizer` 技能后用 `memory_list` / `memory_update` / `memory_delete` / `memory_move` 合并重复、删除失效项，并把明显属于用户偏好或个人事实、但落在当前 Agent 记忆里的条目归位到 user memory；只更新/删除/归位、不新增事实；范围是 user memory 和当前 Agent 的 agent memory。首次使用时会自动为当前 Agent 启用该技能；
- **自动整理**：每个 Agent 累计用户回合（网页与微信消息，定时任务和重试不算），达到配置页「运行环境」设置的间隔（默认 10，0 关闭）时自动发起一次「整理记忆」Run 并把计数归零；生成的会话标题为「记忆整理」，与手动整理使用同一条指令和技能。只对开启「自动学习记忆」的 Agent 生效；反复手动整理不会重复触发；
- 每次 Run 只注入与本轮输入相关的记忆：中文按双字组（过滤停用词）、英文按词元匹配，user 与当前 Agent 同池按相关度竞争（不做 scope 配额或 user 全量注入）；最多 12 条、1500 字，单条最多 320 字，没有相关记忆时不注入。记忆作为数据块附加到系统提示词，不会自动修改 Agent instructions；记录实际使用的 memory id。
- **回复校验**：网页聊天的每条最终回复在结束前会用 deepagents 的 `RubricMiddleware` 校验一次，rubric 由当前 Agent 的 instructions 和全部用户记忆（不含 agent 记忆）组成；评分器可以调用 `session_search` / `session_read` 核对历史对话，评分失败时让主 Agent 修订一次再确认，聊天里只保留修订版，两次评分结果和未通过项显示在消息的「执行过程」里。评分器失败时保留当前回复，不阻断 Run；没有用户记忆时不校验。

## 渠道（微信个人号）

侧栏「渠道」按当前 Agent 管理渠道；一个 Agent 可以绑定多个微信渠道。

- 新建渠道后立即显示 iLink 二维码；用微信扫码并在手机上确认，状态变为「已连接」。
- 登录成功后，平台在 API 进程内为该渠道启动一个 `getupdates` 长轮询任务；不需要公网入口、Webhook、Node 或第三方网关。
- 每个与 bot 私聊的联系人映射为一个独立 Session（`微信 · <联系人>`），在网页会话列表里可见并可继续对话；群聊忽略。图片和文件会从 iLink CDN 下载、AES-128-ECB 解密后保存到该 Agent 的 `uploads/`，并以附件清单进入会话；文本引用会带上引用正文（iLink 引用回包通常不含原文，平台按 `msg_id` / `create_time_ms` 在本地会话历史中反查，查不到时标注「引用原文不可用」），引用里的图片/文件同样保存。语音/视频不下载、不落盘，纯语音/视频直接回「暂不支持」提示且不创建 Run；文字 + 语音只处理文字并注明忽略。渠道来源的 Run 里，`browser_screenshot` 产生的截图会在 Run 运行期间实时上传 iLink CDN 并以图片消息发回该联系人（登录二维码因此能在微信里扫到）；上传或发送失败只记日志。
- 同一微信会话的连续消息会在 5 秒窗口内合并成一个 Run（最长 15 秒），再按现有队列串行执行；打包完成前不保存 iLink `sync_buf`，服务在窗口内重启时由 iLink 重放消息，重复消息按 `external_message_id` 幂等吸收。Run 完成后**立即**把最终答复发回微信，不做发送轮询。
- 微信里可以发会话命令：`/new`（或 `/clear`）为当前联系人开始一段新会话，旧会话仍保留在网页端；`/status` 查看当前 Agent、会话与运行/排队任务数；`/stop` 取消当前会话中排队或运行中的任务；`/help` 和未知命令返回命令说明。命令不经过 Agent、不产生 Run 或消息，确认回复直接通过 iLink 发送（进程崩溃时可能丢失确认）；`/etc/passwd 是什么` 这类含空格或不符合命令形态的文本照常进入 Agent。
- 网页聊天框也支持同一套命令：`/new`（或 `/clear`）清空当前视图并开始新会话，旧会话保留在侧栏；`/status` 查看当前 Agent、会话与运行/排队任务数；`/stop` 取消当前会话中排队或运行中的任务；`/help` 和未知命令返回命令说明。网页 `/new` 沿用懒创建，下一条消息才真正建会话；命令不经过 Agent、不产生 Run 或消息，回复以聊天流末尾的临时卡片显示。输入框以 `/` 开头时会弹出命令面板，可按前缀过滤，用 `↑`/`↓` 选择、`Enter`/`Tab` 填入、`Esc` 关闭；输入完整命令后按 `Enter` 直接执行。
- 网页端在页面可见时每 5 秒刷新当前 Agent 的会话列表；渠道入站产生的新会话、消息数和运行中 Run 会更新侧栏徽标，当前会话的活动 Run 会自动接入。微信会话带「微信」徽标，输入区提示「网页里发送的消息不会发回微信」。会话按最后一条消息时间排序，后台 Run 完成不会改变位置。
- 发送记录与 Run 终态在同一事务写入；服务崩溃后启动时补发未完成的回复；`attempting` 记录补发时会加「可能重复」提示。失败 Run 回固定错误文案，取消不回信。
- 网页里在这个渠道会话中手动发消息不会推回微信；对失败 Run 点重试会复制渠道关联，重试结果仍会回信。
- 登录过期（iLink `-14` 或 `-2 prepare failed`）时渠道变为「登录过期」，轮询停止；点「重新扫码」重新登录，未发送的回复在恢复后补发。
- 渠道暂停后停止收发；删除渠道会删除登录凭据和渠道绑定，已产生的会话/消息/API 记录保留。
- `bot_token` 与 Provider API Key 一样只存在 `app.db`，接口不返回明文；请保护 `data/app.db` 权限。
- iLink 扫码连接的是 iLink bot 身份，普通微信群事件通常不会下发；v1 支持单聊文字、图片、文件和文本引用，出站只发送文本和浏览器截图，不做语音/视频理解、出站文件或主动推送。
- iLink 协议实现参考 Nous Research 的 [hermes-agent](https://github.com/NousResearch/hermes-agent) Weixin 适配器（MIT），许可声明见 `THIRD_PARTY_NOTICES.md`。

## 定时任务（Cron）

侧栏「Agent 能力 → 定时」按当前 Agent 管理定时任务。

- 新建任务时填写名称、任务内容（每轮发给 Agent 的消息）、5 字段 cron 表达式（分 时 日 月 周）、IANA 时区和是否启用；表达式非法、无法计算下一次触发时间、名称或内容为空、同一 Agent 重名都会被拒绝并显示中文错误。
- 一个任务创建时同时创建一条专属 Session（标题 `定时 · <任务名>`），之后每次触发都在同一会话里创建 user Message + queued Run（`source=cron`）。用户可以在会话里继续追问，追问进入同一 checkpoint，成为后续触发的上下文；**新建任务才新建会话**。
- `CronRuntime` 在 API 进程内每 `CRON_TICK_SECONDS`（默认 20 秒）扫描到期任务；触发复用现有 `RunWorker` 和并发上限，不新增执行器。调度器按单进程部署设计，不要为 API 启动多个 uvicorn worker，否则同一任务可能重复触发。
- 到点时如果任务会话里已有 queued/running 的 Run，本次触发跳过并记录到 `audit_log`，不排队堆积。
- 服务停机跨过多个触发点后恢复，每个任务最多补跑一次，随后按表达式继续计算下一次；暂停后恢复不补暂停期间的计划。
- 时区按任务配置解释（默认 `Asia/Shanghai`），存储仍为 UTC；任务页显示下次触发时间、最近运行状态和「打开会话」入口，支持暂停/启用、编辑、立即运行和删除。
- 暂停或删除任务不会取消已经 queued/running 的 Run；删除任务保留会话与历史；删除会话后，任务在下一次触发时自动重建专属会话。
- 可选推送到微信：每个任务可以显式选择一个渠道下的已有 1:1 联系人会话（`GET /api/channels/{id}/conversations` 列出候选）；触发时在创建 Run 的同一事务写入 `ChannelRunLink`，完成/失败后复用现有 `ChannelDelivery` 分块、重试和补发。渠道暂停时消息等待恢复；渠道或联系人删除时任务目标自动清空；删除定时会话会清理它产生的 link/delivery。未配置目标则只留在网页会话里，群聊不支持，取消的 Run 不发送。
- Agent 归档或暂停时不再触发；永久删除 Agent 会级联删除它的定时任务。
- 定时 Run 与普通 Run 使用相同的 Agent 配置和工具权限（包括宿主机 `execute` 与 QuickJS `eval`），prompt 由你配置；平台只应暴露在可信网络中。
- Agent 使用：在「技能」页为当前 Agent 启用 `cron-scheduler`，然后在对话里让它读取技能并管理任务，例如“每天早上 8 点总结昨天工作区的变化，放到一个单独的会话里”。Agent 会用 `cron_create` 建任务和专属会话，用 `cron_list` 查询、`cron_update` 暂停或修改、`cron_delete` 删除、`cron_run` 立即运行；需要推送到微信时先用 `cron_targets` 查看渠道和联系人，再同时传两个目标 id。创建时 prompt 必须自包含，因为任务会话不继承当前对话。
- 不做主动推送；结果只落在会话里，由用户打开查看。

接口：`GET/POST /api/cron-jobs`、`PUT/DELETE /api/cron-jobs/{id}`、`POST /api/cron-jobs/{id}/run`。

## 知识库（坚果云同步）

Agent 可以通过 `/knowledge/` 直接读写坚果云上的个人知识库，用来查笔记、整理资料和写新笔记。

- 平台在生产机维护一份本地镜像（默认 `~/.super-personal-platform/knowledge/`），由 `KnowledgeSyncRuntime` 在 API 进程内与坚果云 WebDAV 双向同步：服务启动后立即同步一次（已配置时），之后按配置页的间隔增量同步（默认 300 秒）。只传输新增或变化的文件，不监听文件系统。
- 配置在「配置」页的「知识库」tab：启用开关、WebDAV 地址（例如 `https://dav.jianguoyun.com/dav/`）、账号、应用密码（坚果云「第三方应用密码」，不是登录密码）、根目录、同步间隔。保存后立即唤醒同步，不需要重启服务；密码保存在 `app.db`，接口只返回 `has_password`。`.env` 的 `KB_WEBDAV_*` 只在数据库里还没有配置时作为首次启动的初始值，方便无界面部署；之后以配置页为准。根目录必须是已存在的目录；根目录不存在时这一轮同步会报错并保留本地镜像，不会把它当成空目录清空。
- 同步规则：远端和本地都改过同一文件时，远端版本保留原名，本地版本另存为 `<name>.conflict-local-<时间戳>` 并上传；远端删除且本地未修改时删除本地；本地删除不传播，远端文件会在下一轮恢复。单文件默认上限 50 MB（`KB_MAX_FILE_BYTES`），超出跳过；路径限制在配置的根目录内，拒绝 `..` 和符号链接。
- 坚果云限流：免费账号每 30 分钟约 600 次 WebDAV 请求（付费 1500 次，所有第三方应用共享），单次 `PROPFIND` 最多 750 项，且不支持 `Depth: infinity`。平台默认每 4.5 秒最多发一个请求（`KB_REQUEST_INTERVAL_SECONDS`），默认跳过 `.obsidian`、`.git`、`.trash`、`node_modules`、`.DS_Store`（`KB_EXCLUDE_DIRS`）；收到 `BlockedTemporarily` 时暂停约 30 分钟（`KB_BLOCKED_RETRY_SECONDS`）。列表达到 750 项、被截断或某个子目录读取失败时，本轮不做删除判断。首次同步大目录会持续较长时间，建议把根目录指向专门的笔记子目录。更改 WebDAV 地址、账号或根目录时，本地镜像和同步状态会移到 `knowledge.old-<时间戳>` / `knowledge_sync_state.json.old-<时间戳>`，再按新配置重建，避免旧文件被上传到新根目录。
- Agent 使用：在「技能」页为当前 Agent 启用 `knowledge-base`，之后它就能用现有文件工具 `ls`、`glob`、`grep`、`read_file`、`edit_file` 操作 `/knowledge/`；未启用的 Agent 不挂载该路径。写新笔记默认放在根目录、文件名用 `YYYY-MM-DD-标题.md`（以技能正文和你知识库根目录的 `README.md` 约定为准），写入本地镜像后几分钟内同步回坚果云。
- 凭据只存在于 API 进程和 `app.db`，不返回前端、不写日志；Agent 能看到目录内容，但看不到 WebDAV 账号。
- 接口：`GET/PUT /api/knowledge` 读取和保存配置（响应包含同步状态，不含密码），`POST /api/knowledge/sync` 立即触发一次同步；`GET /api/system` 的 `knowledge` 字段也返回同步状态。冲突文件的保留提示只写日志，不主动推送。

## 量化回测

顶部「功能」tab 的「回测」是独立页面，不绑定 Agent；首版只做加密货币现货、单标的、历史回测，不接实时行情与实盘交易。

- 行情：Bitget 公共 REST（无需 API Key），按需抓取并增量缓存到 `data/market.db`。周期支持 `1min/3min/5min/15min/30min/1h/4h/6h/12h/1day/1week/1M`；日/周/月使用 Bitget 的 UTC 变体（`1Dutc/1Wutc/1Mutc`）对齐，回测只使用已完成的 K 线。同一交易对和周期第二次回测只抓取缺失的首尾区间；每次回测还会扫描区间内的中间缺口并尝试补齐（`MARKET_GAP_REPAIR_BARS` 默认 50000 根、`MARKET_GAP_REPAIR_RANGES` 默认 50 段）。
- 网络：`api.bitget.com` 在部分网络无法直连，需要在 `~/.super-personal-platform/.env` 设置统一的 `HTTP_PROXY`/`HTTPS_PROXY`（生产机为 `http://192.168.1.2:7893`）。这些变量会作用于所有出站 HTTP 调用（模型、搜索、微信、行情）。页面「测试连接」或 `GET /api/market/probe` 可诊断代理与交易对。
- 策略：在页面内编写 Python，接口为 `def strategy(df) -> pd.Series`。`df` 是 UTC `DatetimeIndex`，列为 `open/high/low/close/volume`；返回每根 K 线的目标仓位 `[-1, 1]`（负数为模拟做空）。策略写入临时文件后在独立子进程中执行，默认超时 120 秒、内存上限 2 GB；这是崩溃隔离，不是安全沙箱。
- 数据完整性：页面「预热 K 线」默认 200 根（0–5000），会在评估区间之前额外加载，策略可在预热段建立初始仓位，指标只从开始日期计算。补不齐的缺口会以 NaN 行保留在策略输入中，缺口期间强制空仓、跨缺口收益不计入。平台还会把策略在截断前缀上重跑并比较重叠信号，发现「疑似使用未来数据」时在结果里告警；该检测会让策略代码再执行一次，策略应保持无副作用。可用 `BACKTEST_FUTURE_CHECK=false` 关闭该检测。
- 结果：总收益、年化、最大回撤、Sharpe、Sortino、胜率、交易次数、盈亏比、基准买入持有；价格 K 线与买卖点、净值、回撤、交易明细。净值/回撤/价格曲线下采样到约 2000 点，交易明细最多保存 5000 笔。结果持久化在 `backtests` 表，可在历史列表回看。
- 任务：`queued/fetching/running/completed/failed/cancelled`；服务重启后未完成任务重新排队；同时只执行一个回测，其余排队。
- 限制：单次回测最多 `BACKTEST_MAX_BARS`（默认 200000）根 K 线；缺口超过修复预算时保留为空仓，并在结果里告警；模拟做空不含借币成本、资金费率与强平。
- Agent 使用：在「技能」页为当前 Agent 启用 `quant-backtest`，然后在对话里让它读取技能并跑回测，例如“用 20/60 日均线策略回测 BTCUSDT 最近 90 天的 1h 数据”。Agent 会使用 `backtest_run` 创建任务（最多等待 90 秒）、`backtest_status` 查询进度、`backtest_result` 查看指标和交易；网页和 Agent 共用同一套任务与行情缓存。

## 交易记录

「交易记录」在功能工作区有独立页面，也可以由 Agent 通过 `trade-journal` 技能读写。记录保存在 `data/trade-journal.jsonl`，一行一笔，便于查看、复制和备份；不新增数据库表。页面支持状态/市场/关键词筛选、新建/编辑、平仓和统计；接口为 `GET/POST /api/trade-journal`、`GET /api/trade-journal/stats`、`GET/PUT /api/trade-journal/{id}`、`POST /api/trade-journal/{id}/close`。

- 字段：`symbol`、`direction`、`entry_at`、`entry_price`、`thesis` 必填；`exit_at`、`exit_price` 平仓时同时提供；可选 `market`、`currency`、`instrument_type`、`stop_loss`、`quantity`、`fees`、`review`、`tags`、`followed_plan`、`meta`。
- 市场：`crypto` 默认 `USDT`/`spot`，代码如 `BTCUSDT`；`a_share` 默认 `CNY`/`stock`，代码建议 `600519.SH`、`000001.SZ`、`300750.SZ`、`688981.SH`。`market` 可省略，平台会从代码推断。
- 计算：`status`、价格收益、R 倍数、净盈亏、胜负由平台计算，不写入文件；没有 `quantity` 时只有价格收益和 R，没有金额盈亏；多币种盈亏不直接相加。
- 标签：用 `tags` 数组，推荐命名空间 `setup:`、`mistake:`、`emotion:`、`regime:`、`board:`、`sector:`、`limit:`；`thesis`/`review` 是全文检索的文本，`meta` 放市场特有、暂不检索的细节。
- 写入：服务层按进程内事件循环串行写入，更新用临时文件原子替换，文件权限 0600；文件损坏行会被跳过，不影响其他记录。
- 页面使用：功能工作区 →「交易记录」；点「新建记录」填写交易对、市场、方向、入场时间/价格、理由；平仓时把状态改成「已平仓」并补出场时间/价格。列表按状态、市场、关键词筛选，右侧统计随记录更新。页面不提供删除。
- Agent 使用：在「技能」页为当前 Agent 启用 `trade-journal`，让它读取 SKILL.md 后，就可以说“记一笔：11 月 2 日 10:15 买 600519.SH，1450，止损 1418，100 股，理由是日线回踩 20 日线缩量企稳”。平仓后说“1498 出了，复盘一下”，Agent 会用 `trade_journal_close` 补出场和复盘；之后用 `trade_journal_query` / `trade_journal_stats` 按标签、市场、时间复盘。
- 边界：不接交易所、不自动下单、不计算实时浮盈、不删除记录；A 股对 T+1、数量整数和止损方向做提示，涨跌停、费用和交易日历不在第一版精确计算。

## 资产配置

功能工作区「交易」组提供独立的长期组合账本，不绑定 Agent，也**不读取交易记录**。它记录的是长期资金、持仓、目标比例和再平衡纪律，而不是短线交易的入场、止损和复盘。

- 组合：可建多个命名组合，每个组合一个基础币种（USDT、CNY……）；组合内不做汇率换算，跨组合在顶部「总览」按 USD 折算合计（USD 与 USDT 按 1:1，CNY 按自动抓取的 USD/CNY 汇率）。
- 流水：入金、出金、初始持仓、买入、卖出、分红、费用；一行一笔存在 `data/allocation-ledger.jsonl`。`初始持仓` 不改变现金，用于录入已有仓位；初始现金另记入金。
- 推导：按时间重放流水，现金、数量、移动加权成本、已实现/未实现盈亏、投入本金、总收益和 XIRR 都由流水算出；卖出超过当时持仓会被拒绝；现金可以为负并提示。
- 目标：每个组合可设现金目标、标的目标占比和再平衡带宽（默认 5 个百分点）；目标合计不等于 100 只提示。页面显示实际 vs 目标、偏离和再平衡金额建议。
- 价格：`GET /api/allocation` 只读配置、流水、手动价和 `market.db` 缓存；点「刷新全部账本」才抓所有组合的 Bitget 1min 最新价，打开页面时也会自动刷新一次（同一浏览器会话 60 秒内不重复请求，失败保留旧价格并提示）。刷新同时抓 USD/CNY 汇率（东方财富 `133.USDCNH`，失败用 Yahoo `CNY=X` 兜底），成功后保存到 `allocation.json`，失败沿用上次值。优先级：实时 > 手动价 > 缓存 > 成本价；裸 ticker（如 `ETH`）会映射为 `ETHUSDT` 再请求；美股 ticker（如 `SPY`）先走东方财富 push2（105/106/107），失败再用 Yahoo Finance chart v8 兜底（Yahoo 需要代理，USD 计价，USDT 组合近似 1:1）；6 位 A 股基金代码（如 `000307`）通过东方财富基金接口抓最新公布净值，A 股股票按手动价。A 股股票、手动价和利息不会自动更新。
- 固定收益：在组合配置里给标的设置年化利率、起息日和是否复投，每次读取按天累计到持仓市值和浮盈（单利或按年复利，按当前持仓成本计算）；配置了计息的标的刷新时不再抓行情。因后续加仓也按整个成本从起息日累计，只适合一次买入后长期持有的仓位。
- 总览：页面顶部的「总览」把所有组合折算成 USD 合并：显示总资产、现金、投入本金、浮盈、已实现、总收益、总收益率和近似 XIRR（历史现金流按当前汇率折算），以及一张合并持仓明细（同一标的跨组合合并，数量、均价、市值、占比和盈亏全部按 USD）。不支持的币种不计入并提示。
- 页面：功能 → 交易 → 资产配置；不分 tab、不按组合分块。上面是总览和合并持仓，中间是所有组合合并的流水（可按组合/标的筛选，金额与价格折算成 USD），最下面是组合设置（每个组合的目标、再平衡和编辑/记一笔入口）。
- 接口：`GET /api/allocation`、`POST /api/allocation/refresh`、`POST/PUT/DELETE /api/allocation/portfolios...`、`GET/POST /api/allocation/portfolios/{id}/entries`、`PUT/DELETE /api/allocation/portfolios/{id}/entries/{entry_id}`。
- Agent 使用：在「技能」页为当前 Agent 启用 `allocation`，读取 SKILL.md 后即可说“看看我的长期组合”“帮我记一笔入金/买入”“把 BTC 目标调到 40%”。Agent 会用 `allocation_overview` / `allocation_entries` 读取，用 `allocation_entry_record` / `allocation_portfolio_update` 写入；不提供删除，删除请在页面处理。
- 边界：不接券商/交易所、不自动下单、不自动再平衡；没有每日估值快照，所以不做净值曲线、最大回撤和收益归因；总览的汇率和 XIRR 为近似值（历史现金流按当前汇率折算）；不做税务批次、分红税、送转股、做空、保证金或借币成本。

## 缠论图

功能工作区「交易」组提供只读的缠论结构图，不绑定 Agent，也不产生交易信号。

- 数据：复用 Bitget 公共 REST 和 `market.db` 增量缓存，只使用已收盘 K 线；按需刷新，不引入 WebSocket。分析范围用 UTC 时间窗口（默认最近 500 根对应窗口，最多 1000 根）。
- 范围：crypto 现货、`15min/1h/4h/1day/1week`；第一版画包含处理、分型、老笔、中枢色块、笔级别 1/2/3 买卖点和 MACD(12,26,9) 副图与背离标记，不画线段和多级别。
- 算法：确定性程序计算，不调用 AI。包含处理向上取高高、向下取低低；分型为严格三根顶/底；老笔顶底交替且至少 5 根合并 K 线（含两端分型）；笔一旦成立不会因后续价格越过它的起点而回滚，只有最后一笔会随更极端的同向分型延伸；未成笔分型不标注；最后一笔之后的未确认走势用虚线画出（同向延伸 + 随后反向极值），不计入笔数、不参与中枢/背离/买卖点；中枢必须由进入段 + 连续三笔重叠 + 确认离开段构成，`ZG/ZD` 取三笔高点最小值与低点最大值；离开段要收在 `ZG` 上方或 `ZD` 下方，且下一笔不回到中枢，刺穿后回到中枢的笔算延伸；数据末端尚未走出或离开段未确认的结构、与上一中枢首尾相接的残留结构不计为中枢；图上的中枢框从第一笔中枢笔起点画到离开段终点（相邻中枢框首尾相接），`ZG/ZD` 仍取三笔重叠；1/2/3 买卖点用 MACD 面积背驰、回踩不破和离开中枢后第一笔回抽不入中枢的笔级别近似规则，后续价格跌破/突破参照价后标记为已失效（页面置灰）。MACD 副图显示 DIF/DEA/柱，背离按同向笔（间隔一笔）比较：价格创新低/高且柱面积收缩到前一同向笔的 90% 以下记为底/顶背离；它是 1 买卖点的来源，是简化动量判断，不是严格缠论背驰。
- 接口：`POST /api/chan/analyze`，入参 `symbol/granularity/bars`，返回 `candles/merged/fractals/strokes/zones/points/macd/divergences/rules/warnings`；不建新表、不落库。
- 页面：交易 → 缠论图；顶部工具栏直接显示参数和显示项；可切换周期、K 线根数和显示项；中枢用半透明色块呈现，买卖点标为 1买/2买/3买、1卖/2卖/3卖；MACD 副图与主图时间轴、十字线同步，背离标为「底背 / 顶背」，可用「MACD」「背离」开关隐藏。交易对、周期、K 线根数、结构/回测模式和各显示开关会写入 `/chan?...` 查询串，刷新或收藏链接后恢复同一套配置；参数变化只替换当前历史项，不新增回退步骤。
- Agent 使用：在「技能」页为当前 Agent 启用 `chanlun`，读取 SKILL.md 后即可说“用缠论看一下 BTCUSDT 1h 最近 300 根，1 买/2 买在哪里”。Agent 会调用 `chan_analyze` 读取同一套结构摘要；它不会重画结构，买卖点只是笔级别近似，不是交易建议。
- 信号回测：缠论图页的「信号回测」tab 用因果前缀回放，信号收盘确认、下一根开盘进场；可设止损、单笔风险、R 倍目标、最大持仓和费用，输出收益、回撤、胜率、平均 R、按信号类型统计和交易明细。页面和 Agent 的 `chan_backtest` 共用同一套逻辑；样本少或没有交易时会明确提示。
- 实时性：Bitget REST 按需拉取，单用户、单标的、单周期刷新压力可以忽略；15min/1h/4h/1day/1w 不需要秒级刷新。需要 Tick 级推送时再做 WebSocket。

## 数据存储

生产数据都在 `~/.super-personal-platform/data/`：

- `app.db`：SQLite（WAL），文件权限 600；
  - `agents`：Agent 名称、instructions、Provider、`enabled_skills`（启用的技能名 JSON 列表）；
  - `sessions`：会话，首次发送消息时才创建；标题取第一条用户消息前 24 个字；`origin='cron'` 标记定时任务会话；会话摘要返回 `active_run_id` / `active_run_status`、`origin` 与 `channel_type` / `channel_name`；
  - `messages`：每条用户/assistant 消息一行，assistant 消息在 Run 完成时写入；
  - `runs`：每次发送/重试/定时触发一行，含 `source`（`manual` / `retry` / `wechat` / `cron`）、状态、input/output、错误、token 用量、时间；
  - `run_events`：append-only，含 `run_queued`、`assistant_delta`（每个流式片段一行）、`reasoning_delta`（思考片段）、`model_usage`、`run_completed`、`memory_saved` 等；
  - `cron_jobs`：定时任务配置、专属 Session、`next_run_at`（UTC）、最近运行/跳过时间；任务删除后 Session 保留，Session 删除后任务解绑并在下次触发时重建；
  - `memory_items`：长期记忆，`scope=user|agent`，含 content、tags、来源 Run/Message、last_used_at；
  - `run_memories`：某次 Run 实际注入了哪些记忆；
  - `providers`：Provider 配置，API Key 存在这里，接口只返回 `has_api_key`，不返回明文；依赖 `app.db` 的文件权限保护。
  - `channels`：渠道及其 iLink 登录凭据（`bot_token` 不返回前端、不写日志）；`channel_conversations`：外部联系人与 Session 的映射（含最新 `context_token`）；`channel_inbounds`：入站消息幂等键；`channel_run_links`：哪些 Run 需要回信；`channel_deliveries`：Run 终态的发送记录，用于崩溃恢复。
  - `backtests`：回测任务与结果，含状态、策略代码、指标、下采样净值/回撤/价格、交易明细和告警；大数组按上限截断。
  - `knowledge_configs`：知识库 WebDAV 配置（单行），含启用开关、地址、账号、应用密码、根目录和同步间隔；密码不返回前端。
  - `app_configs`：平台设置（单行），保存会话自动清理的保留天数和自动记忆整理间隔。
- `market.db`：Bitget K 线增量缓存（WAL），表 `market_candles`（按 `symbol + granularity + open_time_ms` 唯一）和 `market_sync_state`（已同步范围与上次错误）。可以独立删除，下次回测会重新抓取。
- `trade-journal.jsonl`：交易记录，一行一个 JSON 对象，权限 0600；由 `trade-journal` 技能的工具读写，不建数据库表。字段和计算口径见「交易记录」；可以独立备份、复制和手工查看。
- `allocation.json`：长期资产组合配置，权限 0600；保存组合名称、基础币种、备注、现金目标、再平衡带宽、目标占比和手动价。见「资产配置」；不建数据库表。
- `allocation-ledger.jsonl`：长期资产组合流水，一行一个 JSON 对象，权限 0600；入金/出金/初始持仓/买入/卖出/分红/费用，按时间重放得到现金、持仓和收益。见「资产配置」；不建数据库表。
- `browser_state.json`：浏览器 cookies 与 localStorage（Playwright storage_state），权限 0600；用于跨 Run 保持网站登录态，删除后所有站点需要重新登录。没有配置时不会创建。
- `checkpoints/{session_id}.sqlite`：deepagents/LangGraph 的会话状态，用于继续同一 session 的对话；平台消息以 `messages` 为准。
- `messages_fts`：`messages.content` 的 FTS5 trigram 索引（external content，触发器同步）；用于 Agent 的 `session_search` / `session_read` 和侧栏搜索，首次创建或索引为空时自动 `rebuild` 回填。FTS 不可用时降级 `LIKE`。
- `~/.super-personal-platform/workspaces/`：每个 Agent 的固定工作区目录，名称为 Agent id；删除 Agent 不会删除这里的文件。`screenshots/<session_id>/<run_id>/` 保存该 Agent 的浏览器截图，删除会话时清理对应目录。
- `~/.super-personal-platform/skills/`：全局技能库，由用户/运维维护；删除技能不会自动清理 Agent 的启用列表，运行时忽略不存在的技能。
- `~/.super-personal-platform/knowledge/`：坚果云知识库的本地镜像，由后台同步任务维护；可以独立删除，下次同步会重新下载。`data/knowledge_sync_state.json` 记录远端 etag 与本地哈希，删除后下一轮同步会按「两边都变」的规则重新对账。

备份时停服务后复制 `data/app.db`、`data/checkpoints/`、`data/trade-journal.jsonl`、`data/allocation.json` 和 `data/allocation-ledger.jsonl`；`data/market.db` 是可按需重建的行情缓存，建议一并备份以免重新抓取。`knowledge/` 是坚果云镜像，可不备份；`data/knowledge_sync_state.json` 丢失后下一轮同步会重新对账。删除 session 会同时删除它的 messages、runs、run_events 和 checkpoint。

## 配置

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_HOME` | 配置与数据根目录 | `~/.super-personal-platform` |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `APP_DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite+aiosqlite:///$APP_HOME/data/app.db` |
| `APP_DATA_DIR` | 数据/checkpoint 目录 | `$APP_HOME/data` |
| `APP_WORKSPACES_DIR` | 真实工作区根目录 | `$APP_HOME/workspaces` |
| `APP_SKILLS_DIR` | 全局技能库根目录 | `$APP_HOME/skills` |
| `APP_KNOWLEDGE_DIR` | 知识库本地镜像目录 | `$APP_HOME/knowledge` |
| `KB_WEBDAV_URL` | 知识库初始配置：仅当数据库还没有配置时写入；之后在配置页「知识库」管理 | 空 |
| `KB_WEBDAV_USERNAME` | 知识库初始 WebDAV 账号 | 空 |
| `KB_WEBDAV_PASSWORD` | 知识库初始 WebDAV 应用密码（保存后只在 app.db，不返回前端） | 空 |
| `KB_WEBDAV_ROOT` | 知识库初始根目录（相对 WebDAV 根） | 空 |
| `KB_SYNC_INTERVAL_SECONDS` | 知识库初始同步间隔（秒，最小 30）；之后由配置页管理 | `300` |
| `KB_SYNC_TIMEOUT_SECONDS` | 单次 WebDAV 请求超时（秒） | `30` |
| `KB_MAX_FILE_BYTES` | 单个知识库文件的大小上限 | `52428800` |
| `KB_REQUEST_INTERVAL_SECONDS` | WebDAV 请求最小间隔（秒），用于遵守坚果云限流 | `4.5` |
| `KB_EXCLUDE_DIRS` | 同步时跳过的目录/文件名（逗号分隔） | `.obsidian,.git,.trash,node_modules,.DS_Store` |
| `KB_MAX_LISTING_ENTRIES` | 单次 PROPFIND 的条目上限；达到后本轮不做删除判断 | `750` |
| `KB_BLOCKED_RETRY_SECONDS` | 收到 `BlockedTemporarily` / HTTP 503 后的暂停秒数 | `1800` |
| `BROWSER_CONCURRENCY` | 每个 Run 的浏览器标签页上限，同时决定不同 session 的 Run 并发数 | `5` |
| `BROWSER_NORMAL_IDENTITY` | 使用普通桌面浏览器身份（真实 UA、locale、隐藏 webdriver、关闭自动化特征） | `true` |
| `BROWSER_USER_AGENT` | 自定义 UA；留空时按实际 Chromium 版本生成普通 Chrome UA | 空 |
| `BROWSER_LOCALE` | 浏览器语言 | `zh-CN` |
| `BROWSER_TIMEZONE` | 浏览器时区 | `Asia/Shanghai` |
| `BROWSER_ACCEPT_LANGUAGE` | Accept-Language 请求头 | `zh-CN,zh;q=0.9,en;q=0.8` |
| `BROWSER_PERSIST_STATE` | 把 cookies/localStorage 保存到 `browser_state.json` 并跨 Run 复用 | `true` |
| `BROWSER_STATE_PATH` | 登录态文件路径；留空时为 `$APP_DATA_DIR/browser_state.json` | 空 |
| `BROWSER_NAVIGATION_TIMEOUT_MS` | 页面导航超时 | `30000` |
| `BROWSER_ACTION_TIMEOUT_MS` | 点击/填写动作超时 | `10000` |
| `BROWSER_MAX_ELEMENTS` | 每次快照的交互元素上限 | `120` |
| `BROWSER_MAX_TEXT_CHARS` | 每次快照回传的正文上限 | `4000` |
| `BROWSER_MAX_SNAPSHOT_CHARS` | 单次快照总字符上限 | `12000` |
| `BROWSER_SCREENSHOT_MAX_BYTES` | 单张截图大小上限 | `4000000` |
| `BROWSER_NO_SANDBOX` | Chromium 启动失败时的逃生开关 | `false` |
| `CRON_TICK_SECONDS` | 定时任务扫描间隔（秒） | `20` |
| `CRON_DEFAULT_TIMEZONE` | 新建定时任务的默认 IANA 时区 | `Asia/Shanghai` |
| `LLM_BASE_URL` | OpenAI-compatible 端点 | `https://api.deepseek.com` |
| `LLM_API_KEY` | 模型密钥 | 空 |
| `LLM_MODEL` | 模型名 | `deepseek-v4-flash` |
| `CHANNEL_REPLY_MAX_CHARS` | 微信回复单块字符上限（在段落/代码围栏边界切块） | `1800` |
| `CHANNEL_DELIVERY_MAX_ATTEMPTS` | 单次 Run 发送与补发的尝试上限 | `3` |
| `CHANNEL_INBOUND_MAX_CHARS` | 单条微信入站文本上限 | `8000` |
| `CHANNEL_INBOUND_BATCH_SECONDS` | 同一联系人连续消息的打包窗口（秒，0 关闭） | `5` |
| `CHANNEL_INBOUND_BATCH_MAX_SECONDS` | 连续消息不断刷新时的最长打包时间（秒） | `15` |
| `CHANNEL_MEDIA_MAX_BYTES` | 单个微信图片/文件下载上限 | `20971520` |
| `CHANNEL_MEDIA_MAX_ITEMS` | 单条微信消息最多下载的媒体数 | `4` |
| `VISION_MAX_IMAGES` | 单轮发送给模型的图片上限 | `4` |
| `VISION_MAX_IMAGE_BYTES` | 单张图片发送给模型的上限；超过时先压缩 | `4194304` |
| `VISION_MAX_IMAGE_EDGE` | 超大图片压缩后的最长边（像素，最小 320） | `1600` |
| `SESSION_RETENTION_DAYS` | 会话保留初始值（天）；仅在首次创建配置时写入，之后在配置页「运行环境」修改，`0` 表示不清理 | `30` |
| `MEMORY_CONSOLIDATE_EVERY_TURNS` | 自动记忆整理间隔初始值（用户回合）；仅在首次创建配置时写入，之后在配置页「运行环境」修改，`0` 表示关闭 | `10` |
| `SESSION_CLEANUP_INTERVAL_SECONDS` | 会话清理扫描间隔（秒，最小 60） | `3600` |
| `WEIXIN_LONG_POLL_TIMEOUT_MS` | iLink `getupdates` 长轮询窗口 | `35000` |
| `WEIXIN_API_TIMEOUT_MS` | iLink `sendmessage` 请求超时 | `15000` |
| `WEIXIN_QR_TIMEOUT_MS` | iLink 二维码请求/状态轮询超时 | `35000` |
| `BITGET_BASE_URL` | Bitget 公共 REST 地址 | `https://api.bitget.com` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 统一出站 HTTP 代理，LLM、搜索、微信和行情共用；由 `settings` 导出到进程环境 | 空 |
| `NO_PROXY` | 不走代理的主机列表 | 空 |
| `MARKET_HTTP_TIMEOUT_SECONDS` | 单次行情请求超时 | `15` |
| `MARKET_RATE_LIMIT_PER_SECOND` | 行情请求限速（次/秒） | `5` |
| `MARKET_MAX_RETRIES` | 429/5xx/网络错误重试次数 | `3` |
| `MARKET_GAP_REPAIR_BARS` | 单次回测最多补齐的中间缺口 K 线数 | `50000` |
| `MARKET_GAP_REPAIR_RANGES` | 单次回测最多处理的缺口段数 | `50` |
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

生产不再构建 Web 镜像：FastAPI 直接托管已提交的 `apps/web/dist`（含 SPA fallback），API 和页面在同一个进程里。

## V1 生产部署（宿主机 systemd + SQLite）

生产机：`qiuqiu@192.168.1.3`，代码目录 `~/SuperPersonalPlatform-v1-deploy`，监听 8888。代码从 GitHub `main` 拉取；前端由 FastAPI 直接托管，配置和数据在 `~/.super-personal-platform/`。

首次准备：

```bash
cd ~/SuperPersonalPlatform-v1-deploy
python3.12 -m venv .venv
.venv/bin/pip install -e apps/api
.venv/bin/playwright install chromium
mkdir -p ~/.super-personal-platform/data
cp deploy/.env.example ~/.super-personal-platform/.env
# 编辑 ~/.super-personal-platform/.env，填 APP_AUTH_TOKEN、LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
# 量化回测/外网访问需要代理时，设置 HTTP_PROXY 和 HTTPS_PROXY（生产机可用 http://192.168.1.2:7893）
# 知识库优先在配置页的「知识库」tab 配置；KB_* 仅作为首次启动的初始值
cp deploy/agent-command-center.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-command-center.service
```

从旧布局迁移（`deploy/.env` + 部署目录 `data/`）：

```bash
# 先备份 data/ 和 deploy/.env
systemctl --user stop agent-command-center.service
# 确认旧 ~/.super-personal-platform 只有历史数据后再清空
rm -rf ~/.super-personal-platform
mkdir -p ~/.super-personal-platform
mv ~/SuperPersonalPlatform-v1-deploy/data ~/.super-personal-platform/data
mv ~/SuperPersonalPlatform-v1-deploy/deploy/.env ~/.super-personal-platform/.env
cp ~/SuperPersonalPlatform-v1-deploy/deploy/agent-command-center.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

迁移前确认旧 `~/.super-personal-platform/` 只有历史数据（用户已确认清空）；迁移后核对会话、消息、Run 和记忆数量。

日常更新：

```bash
cd ~/SuperPersonalPlatform-v1-deploy
git pull --ff-only origin main
# 只有 pyproject 依赖变化时才需要重新安装
.venv/bin/pip install -e apps/api
.venv/bin/playwright install chromium
systemctl --user restart agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

`deploy/agent-command-center.service` 使用 `~/.super-personal-platform/.env` 作为 `EnvironmentFile`，并强制 `APP_DATA_DIR=%h/.super-personal-platform/data`、`APP_DATABASE_URL=sqlite+aiosqlite:///%h/.super-personal-platform/data/app.db`。用户级服务依赖 linger（`loginctl show-user qiuqiu -p Linger` 应为 `yes`），否则退出登录后进程会被回收。

如果 Chromium 启动报缺少共享库，执行 `sudo .venv/bin/playwright install-deps chromium`；仍无法启动时可临时设置 `BROWSER_NO_SANDBOX=true`（会降低浏览器隔离级别）。

回滚 v1 版本：checkout 上一个已验证的提交，重新安装依赖（如需要）并重启：

```bash
cd ~/SuperPersonalPlatform-v1-deploy && git checkout <commit> && systemctl --user restart agent-command-center.service
```

备份：停服务后复制 `~/.super-personal-platform/data/app.db` 和 `data/checkpoints/`；或在服务运行时用 SQLite backup API 导出 `app.db`。

当前生产为宿主机 systemd + SQLite；Docker Compose、Dockerfile、nginx 与 Postgres 的部署配置已从仓库移除。

## 测试

```bash
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

详细需求见 `docs/intent.md`；协作约定见 `CLAUDE.md`。
