# Agent Command Center

v1：网页对话助手（Chat + 多 Agent + Subagent + Checkpoint/Runs + 配置页）与多渠道（微信个人号），浅色界面，assistant 消息支持 Markdown。

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
│   └── checkpoints/     # 每个 session 的 LangGraph checkpoint
├── workspaces/          # Agent 真实工作区根目录
└── skills/              # 全局技能库（每个技能一个目录；.seed_state.json 记录内置技能哈希）
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
- 对话里以“记住：……”开头的内容会写入用户记忆；
- 用户消息和 assistant 消息都按 Markdown 渲染：GFM 表格/列表、代码高亮、代码块复制、链接和图片；
- 消息操作：复制、引用到输入框；失败 Run 可重试；
- 流式输出、停止生成、自动增高输入框；运行状态按会话保存，切走再切回会继续显示该会话的流式文本、思考过程和截图；发送时强制滚到底部，输出期间上滑后显示浮动的「回到最新」按钮；模型返回 `reasoning_content` 时，聊天中显示可折叠的「思考过程」：思考阶段展开，开始输出答案后自动折叠，完成后保留可展开；
- 多个会话可以各自跟踪自己的 Run：切走 A 去加载 B 时先清空旧消息并显示骨架，B 不会出现 A 的文本；另一个会话的 Run 运行中时当前会话禁用发送并提示，输入框仍可编辑；异步错误只显示在所属 Agent/会话。失败或取消的 Run 会保留已流出的内容，显示「失败」/「已停止」气泡、错误摘要与重试按钮，刷新后从 `run_events` 恢复；
- 后端同一 session 最多一个 running Run，queued Run 可以排队；网页 Chat 与 retry 在已有活动 Run 时返回 409（带 `active_run_id`），前端直接接入该 Run 并提示刚才的消息没有发送、恢复草稿；渠道入站消息允许继续排队并由 worker 串行执行；不同 session 的 Run 最多并发 `BROWSER_CONCURRENCY` 个（默认 2），超出的 Run 保持 `queued`；
- 服务停止/重启时正在运行的 Run 会重新排队并在启动后继续；用户主动取消的 Run 不会被重启复活；仍打开的页面会自动重连（瞬时连接错误按退避重试）；收到 `reason=service_restart` 的 `run_queued` 时清空上一次尝试的流式文本，并显示「服务重启，Run 已重新排队」。恢复时保留中断前的 checkpoint 消息并追加「继续上次任务」提示，不重复原始用户输入；未返回结果的工具调用会补一条结果未知的 error 消息。重新入队不会回滚已经发生的文件/浏览器副作用，恢复提示会要求 Agent 不要重复执行；
- 模型调用遇到 429/5xx/超时/连接错误时最多重试 3 次（指数退避 + jitter）；401/400 等 4xx 不重试；重试在模型层实现，主 Agent、general-purpose 子 Agent 和 step-limit 总结都会覆盖；流式调用已输出 token 后不重试，避免重复文本；可重试失败会记录 warning 日志；
- 单个 Run 的步骤上限默认 500（`RUN_RECURSION_LIMIT`，主 Agent 与子 Agent 共用）；真的达到上限时先在原会话上自动续跑一次，仍超限才用同一模型带上完整对话生成最终答复，Run 仍以 completed 收尾并标注被截断；
- Agent 可在当前工作区内查看、创建和修改文件（ls / read_file / write_file / edit_file / delete / glob / grep）；宿主机 shell 与代码解释器分别由 `local-shell` / `code-interpreter` 技能门控：启用技能并读取对应 SKILL.md 后，`execute`（LocalShellBackend，以服务账号权限在宿主机执行命令，没有沙箱）或 `eval`（QuickJS 沙箱，无文件与网络，PTC 只开放文件工具）才会出现；未启用的 Agent 不注册这些工具。浏览器能力通过 `web-browser` 技能解锁（无头 Chromium，默认返回文字和元素引用；用户明确要求时可返回页面截图，截图保存到 Agent 工作区并显示在对应回复下方）；
- 技能与工具：左侧「技能」页面为当前 Agent 启用技能；模型读取 `/skills/<name>/SKILL.md` 后，技能声明的工具才会解锁；上下文压缩后会自动重新注入已启用技能的正文，保持解锁；文件工具始终可用；「工具」页面只展示注册与声明情况；
- 空状态提供建议提示词；详情抽屉默认关闭，关闭时内容列自动加宽。
- 顶部轻量栏显示当前 Agent 和会话标题，并作为详情抽屉开关。
- 响应式：桌面为侧栏 + 对话 + 详情三栏；窄屏（≤860px）用顶部栏和汉堡菜单打开侧栏抽屉，详情变为全屏面板，输入区跟随软键盘与底部安全区。
- 输入卡片左侧「＋」菜单提供上传附件、整理记忆和清空输入：文件保存到当前 Agent 的 `uploads/` 工作区目录（单文件 10 MB，上传时显示单文件进度和大小），发送后消息里带上文件名、工作区路径和字节数，Agent 可用文件工具读取；聊天里附件显示为文件 chip，「整理记忆」显示为命令卡片（原始指令可展开），不再把内部文本直接铺开；「整理记忆」直接发起一个 Run 并触发 `memory-organizer` 技能；也可以清空输入。

## 配置页

侧栏底部「配置」进入配置页，顶部用 `Provider / Agent / 运行环境` 三个 tab 切换，不需要一路滚到底；Provider 默认只显示列表，点条目展开表单；Agent 列表点击行切换当前 Agent，点「编辑」展开表单。**配置页始终编辑当前 Agent（与侧边栏共用一个选择状态）**；Agent tab 列出全部 Agent 的摘要，点击任意一行即可切换当前 Agent，侧边栏选择器同步变化；编辑区只编辑当前 Agent。切换 Agent 时，Provider tab 会自动选中该 Agent 的 Provider；输入草稿按 Agent 分别保留（当前页面内），切换时旧 Agent 的会话和消息不会短暂显示在新 Agent 下。集中管理：

- Provider：查看全部 Provider、密钥是否已配置、新增和编辑；
- Agent：列表展示全部 Agent 的 Provider/model、启用技能数、自动记忆和状态，点击行切换当前 Agent；下方编辑名称、Provider、instructions、工作区，或新建 Agent；新建时未填写 instructions 会使用默认的中文个人助手提示词（能力说明 + 行为规范）；
- 工作区：每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/`，不支持自定义路径；配置页只读展示机器目录，并随左上角选中的 Agent 变化。Agent 可以查看和修改工作区内的文件；
- 技能：在侧边栏左上角的「技能」页面按当前 Agent 勾选启用；未启用的技能不会进入系统提示，读取它的 SKILL.md 也不会解锁工具。磁盘上已删除但仍在启用列表里的技能会显示为“未找到”，可取消勾选移除；
- Agent 彻底删除：输入 Agent 名称确认后，级联删除它的会话、消息、Run、事件和 checkpoint（有活动 Run 时拒绝）；
- 运行环境：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关（`GET /api/system`）。

Run 事件存放在 `run_events` 表；前端默认折叠 `assistant_delta` / `reasoning_delta` 流式片段，在右侧抽屉里展开查看。`tool_call` 事件记录工具名与解析后的参数，聊天中的活动行据此显示「正在读取 …」「正在浏览 …」，运行超过 10 秒后附上已用时间。

## 技能与工具页面

侧边栏左上角（Agent 选择器下方）提供两个独立页面：

- **技能**：列出全局技能库，按当前 Agent 勾选/取消启用，立即保存、下一轮 Run 生效；展示 description、声明的工具、SKILL.md 路径和解析警告；点「查看」可打开 SKILL.md 全文并直接编辑保存。新建技能仍然在对话里让 Agent 完成。
- **工具**：展示文件工具（始终可用）和技能工具；技能工具显示是否已在平台注册、由哪些技能声明。启用声明它的技能并在对话中读取 SKILL.md 后解锁，没有单独的工具开关。

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
- 技能创建：直接让 Agent 在对话里创建或修改 `/skills/<name>/SKILL.md`，不需要表单；也可以在主机上编辑文件。Agent 写入的技能同样需要为当前 Agent 启用。
- 因为 Agent 可以写技能，技能声明本身不是安全边界；控制点是当前 Agent 启用了哪些技能，启用技能即允许它声明的已注册工具。
- 内置技能会在技能库中 seed：`web-fetch`、`web-search`、`web-browser`、`memory-organizer`、`local-shell` 和 `code-interpreter`，分别声明 `include_tools: web_fetch` / `web_search` / 七个 `browser_*` 工具 / `memory_list memory_update memory_delete` / `execute` / `eval`。写入内容的哈希记录在 `skills/.seed_state.json`：用户未修改的内置技能会随版本自动更新，改过的保持不动；没有记录的既有文件不会被覆盖，直到其内容与内置默认一致后开始跟踪。

## 工具

平台注册表当前提供以下技能工具：

- **`web_search`**：调用 DeepSeek Search API（`POST {provider.base_url}/v1/tools/search`），请求 `query`（1-128 字）、`count`（1-50，默认 10）、`need_content`，返回标题、链接、摘要、来源、日期、权威性评分。支持 `site:`、`-`、引号、`lang:`、`after:`、`before:` 等搜索语法。
- **`web_fetch`**：调用 DeepSeek Fetch API（`POST {provider.base_url}/v1/tools/fetch`），请求 `{url}`，返回 `{title, content}`（Markdown）。`url` 必须是完整 http/https 地址，不能带用户名/密码。
- **浏览器工具**（Playwright，本地无头 Chromium）：
  - `browser_open(url, tab="main")`：打开网页，返回标题、正文和可交互元素 ref；
  - `browser_snapshot(tab="main")`：读取当前标签页的最新快照；
  - `browser_click(ref, tab)`：点击快照中的元素；
  - `browser_fill(ref, text, tab, submit=false)`：填写输入框或 select；
  - `browser_press(key, tab)`：按下按键；
  - `browser_screenshot(tab, full_page=false)`：按需截取当前页面，返回 PNG 图片；只在用户明确要求查看截图/外观/布局时调用，Provider 模型需要支持图片输入；PNG 同时保存到 `workspaces/<agent_id>/screenshots/<session_id>/<run_id>/`，聊天里显示在对应 assistant 消息下方，Agent 文件工具也能访问；
  - `browser_close(tab)`：关闭标签页并释放并发槽位。
  - 每次 Run 懒启动一个浏览器进程和一个 context，最多 `BROWSER_CONCURRENCY=2` 个标签页；Run 结束、失败、超时或取消时自动关闭。ref 只对最近一次快照有效，动作后会返回新快照。
  - 只允许公开 http/https 地址，拒绝 localhost、内网/回环/链路本地地址、带凭据 URL、`file://` 和下载；只处理主 frame，不接管弹窗。
  - 搜索类页面优先尝试移动版/轻量入口（`m.baidu.com`、`www.google.com/search?q=`、`html.duckduckgo.com`、`cn.bing.com`、维基百科）；Google 需要主机网络能直连，当前网络被 DNS 污染时会被地址校验拒绝；遇到验证码、安全验证或 HTTP 403/429 时返回明确错误并提示换入口，不会尝试绕过验证。

`web_search` 和 `web_fetch` 使用当前 Agent Provider 的 key，需要 Provider 支持 DeepSeek Search/Fetch；成功后按请求计费（各每 1000 次 5 元），分别受 DeepSeek QPS 限制（Search 100、Fetch 25）。使用条件：在「技能」页面启用声明它的技能（`web-search` / `web-fetch` / `web-browser`），对话中读取该 SKILL.md 后解锁。

- **`memory_list` / `memory_update` / `memory_delete`**：读取、更新、删除长期记忆（`memory-organizer` 技能声明）；列表返回 id、scope、标签和正文，更新/删除只能作用在 user memory 或当前 Agent 的 agent memory 上，其他 Agent 的记忆返回 not found；不提供新增工具。
- **`execute`**（`local-shell` 技能，不在代码注册表）：启用该技能会把 Agent 的默认 backend 换成 `LocalShellBackend`，读取 SKILL.md 后出现 `execute` 工具。命令通过系统 shell 以服务账号权限运行在工作区目录，但**不受工作区限制、没有沙箱**；子进程只注入 `PATH`、`HOME`、`LANG`，不继承平台密钥环境变量。命令默认 120 秒超时。启用该技能的 Agent 等于被授权操作这台机器。
- **`eval`**（`code-interpreter` 技能，不在代码注册表）：启用该技能会挂载 `CodeInterpreterMiddleware`，读取 SKILL.md 后出现 `eval` 工具，在持久化的 QuickJS 上下文里执行 JavaScript，按会话隔离；默认 5 秒超时、64 MiB、每次最多 256 次 PTC 调用。环境没有文件系统、网络、`require` 或 `process`；通过 `tools.readFile` / `tools.writeFile` 等 PTC 接口调用已解锁的文件工具。PTC 只开放文件工具，不开放 `execute`；子 Agent 调度默认关闭。

`execute` 与 `eval` 都以「当前 Agent 启用技能 + 会话内读取过该 SKILL.md」为条件，读取前不出现在工具列表，直接调用会返回 locked 错误；门控在 `SkillsMiddleware` 槽位实现，主 Agent 与 general-purpose 子 Agent 一致，上下文压缩后仍按读取记录恢复。

工具实现分别在 `apps/api/app/services/skill_tools.py`（Search/Fetch）、`apps/api/app/services/browser_tools.py`（浏览器）和 `apps/api/app/services/memory_tools.py`（记忆）；`execute` 由 `deepagents` 的 `LocalShellBackend` 提供，`eval` 由 `langchain-quickjs` 提供。新增注册表工具时注册一个 factory，按运行时 Provider/会话构建 LangChain `BaseTool`。

## 记忆

侧栏「记忆」进入独立记忆页面：

- **用户记忆**：所有 Agent 共享，适合偏好、长期事实；
- **Agent 记忆**：只对当前 Agent 生效，适合角色和领域知识；
- 手动新增/编辑/删除/搜索；对话里“记住：……”会立即写入用户记忆；
- Agent 编辑区有「自动学习记忆」开关：开启后，Run 完成时最多返回 12 个记忆操作；先用 FTS5（trigram）找出相关历史记忆，再让模型返回 add/update/delete 做合并，去重后直接写入，每个 Agent 上限 200 条；提取在 Run 完成后以后台任务执行，不占用 Run 并发槽，同一 Agent 的提取串行执行，避免并发写入重复记忆；
- **整理记忆**：聊天输入区「＋」菜单的「整理记忆」指令会直接发起一个 Run，读取 `memory-organizer` 技能后用 `memory_list` / `memory_update` / `memory_delete` 合并重复、删除失效项，只更新/删除、不新增事实；范围是 user memory 和当前 Agent 的 agent memory。首次使用时会自动为当前 Agent 启用该技能；
- 每次 Run 最多注入 12 条、约 1500 字相关记忆，作为数据块附加到系统提示词，不会自动修改 Agent instructions。

## 渠道（微信个人号）

侧栏「渠道」按当前 Agent 管理渠道；一个 Agent 可以绑定多个微信渠道。

- 新建渠道后立即显示 iLink 二维码；用微信扫码并在手机上确认，状态变为「已连接」。
- 登录成功后，平台在 API 进程内为该渠道启动一个 `getupdates` 长轮询任务；不需要公网入口、Webhook、Node 或第三方网关。
- 每个与 bot 私聊的联系人映射为一个独立 Session（`微信 · <联系人>`），在网页会话列表里可见并可继续对话；群聊和图片/语音/文件消息忽略。
- 同一微信会话连续发消息会排队创建多个 Run，worker 按 Session 串行执行；Run 完成后**立即**把最终答复发回微信，不做发送轮询。
- 发送记录与 Run 终态在同一事务写入；服务崩溃后启动时补发未完成的回复；`attempting` 记录补发时会加「可能重复」提示。失败 Run 回固定错误文案，取消不回信。
- 网页里在这个渠道会话中手动发消息不会推回微信；对失败 Run 点重试会复制渠道关联，重试结果仍会回信。
- 登录过期（iLink `-14` 或 `-2 prepare failed`）时渠道变为「登录过期」，轮询停止；点「重新扫码」重新登录，未发送的回复在恢复后补发。
- 渠道暂停后停止收发；删除渠道会删除登录凭据和渠道绑定，已产生的会话/消息/API 记录保留。
- `bot_token` 与 Provider API Key 一样只存在 `app.db`，接口不返回明文；请保护 `data/app.db` 权限。
- iLink 扫码连接的是 iLink bot 身份，普通微信群事件通常不会下发；v1 只支持单聊文本，不支持主动推送。
- iLink 协议实现参考 Nous Research 的 [hermes-agent](https://github.com/NousResearch/hermes-agent) Weixin 适配器（MIT），许可声明见 `THIRD_PARTY_NOTICES.md`。

## 数据存储

生产数据都在 `~/.super-personal-platform/data/`：

- `app.db`：SQLite（WAL），文件权限 600；
  - `agents`：Agent 名称、instructions、Provider、`enabled_skills`（启用的技能名 JSON 列表）；
  - `sessions`：会话，首次发送消息时才创建；标题取第一条用户消息前 24 个字；
  - `messages`：每条用户/assistant 消息一行，assistant 消息在 Run 完成时写入；
  - `runs`：每次发送/重试一行，含状态、input/output、错误、token 用量、时间；
  - `run_events`：append-only，含 `run_queued`、`assistant_delta`（每个流式片段一行）、`reasoning_delta`（思考片段）、`model_usage`、`run_completed`、`memory_saved` 等；
  - `memory_items`：长期记忆，`scope=user|agent`，含 content、tags、来源 Run/Message、last_used_at；
  - `run_memories`：某次 Run 实际注入了哪些记忆；
  - `providers`：Provider 配置，API Key 存在这里，接口只返回 `has_api_key`，不返回明文；依赖 `app.db` 的文件权限保护。
  - `channels`：渠道及其 iLink 登录凭据（`bot_token` 不返回前端、不写日志）；`channel_conversations`：外部联系人与 Session 的映射（含最新 `context_token`）；`channel_inbounds`：入站消息幂等键；`channel_run_links`：哪些 Run 需要回信；`channel_deliveries`：Run 终态的发送记录，用于崩溃恢复。
- `checkpoints/{session_id}.sqlite`：deepagents/LangGraph 的会话状态，用于继续同一 session 的对话；平台消息以 `messages` 为准。
- `~/.super-personal-platform/workspaces/`：每个 Agent 的固定工作区目录，名称为 Agent id；删除 Agent 不会删除这里的文件。`screenshots/<session_id>/<run_id>/` 保存该 Agent 的浏览器截图，删除会话时清理对应目录。
- `~/.super-personal-platform/skills/`：全局技能库，由用户/运维维护；删除技能不会自动清理 Agent 的启用列表，运行时忽略不存在的技能。

备份时停服务后复制 `data/app.db` 和 `data/checkpoints/`；删除 session 会同时删除它的 messages、runs、run_events 和 checkpoint。

## 配置

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_HOME` | 配置与数据根目录 | `~/.super-personal-platform` |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `APP_DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite+aiosqlite:///$APP_HOME/data/app.db` |
| `APP_DATA_DIR` | 数据/checkpoint 目录 | `$APP_HOME/data` |
| `APP_WORKSPACES_DIR` | 真实工作区根目录 | `$APP_HOME/workspaces` |
| `APP_SKILLS_DIR` | 全局技能库根目录 | `$APP_HOME/skills` |
| `BROWSER_CONCURRENCY` | 每个 Run 的浏览器标签页上限，同时决定不同 session 的 Run 并发数 | `2` |
| `BROWSER_NAVIGATION_TIMEOUT_MS` | 页面导航超时 | `30000` |
| `BROWSER_ACTION_TIMEOUT_MS` | 点击/填写动作超时 | `10000` |
| `BROWSER_MAX_ELEMENTS` | 每次快照的交互元素上限 | `120` |
| `BROWSER_MAX_TEXT_CHARS` | 每次快照回传的正文上限 | `4000` |
| `BROWSER_MAX_SNAPSHOT_CHARS` | 单次快照总字符上限 | `12000` |
| `BROWSER_SCREENSHOT_MAX_BYTES` | 单张截图大小上限 | `4000000` |
| `BROWSER_NO_SANDBOX` | Chromium 启动失败时的逃生开关 | `false` |
| `LLM_BASE_URL` | OpenAI-compatible 端点 | `https://api.deepseek.com` |
| `LLM_API_KEY` | 模型密钥 | 空 |
| `LLM_MODEL` | 模型名 | `deepseek-v4-flash` |
| `CHANNEL_REPLY_MAX_CHARS` | 微信回复单块字符上限（在段落/代码围栏边界切块） | `1800` |
| `CHANNEL_DELIVERY_MAX_ATTEMPTS` | 单次 Run 发送与补发的尝试上限 | `3` |
| `CHANNEL_INBOUND_MAX_CHARS` | 单条微信入站文本上限 | `8000` |
| `WEIXIN_LONG_POLL_TIMEOUT_MS` | iLink `getupdates` 长轮询窗口 | `35000` |
| `WEIXIN_API_TIMEOUT_MS` | iLink `sendmessage` 请求超时 | `15000` |
| `WEIXIN_QR_TIMEOUT_MS` | iLink 二维码请求/状态轮询超时 | `35000` |

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
