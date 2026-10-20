# Intent

- 状态：现行
- 最后更新：2026-10-20
- 编号水位：F14

本文件是新项目的需求唯一来源。v1 从 0 到 1 重建，旧代码与旧数据模型已废弃；旧需求不再作为实现依据。

## 怎么用

- 新需求在末尾追加 `### F<n> <名称>`，编号不复用。
- 需求只写意图、场景、边界和成功标准，不写技术路线。
- 没有本文件覆盖的改动不进入实现。
- 方案先定后做；涉及新依赖、破坏性接口、数据迁移时先确认。

## 需求记录

### F1 Agent Command Center v1

- 状态：进行中
- 日期：2026-10-19
- 范围：网页 Chat + 多 Agent + 默认 general-purpose Subagent + Checkpoint/Runs
- 前置依赖：无（旧系统已废弃）

#### 背景与目标

旧平台整体推翻，从 0 到 1 重建一个单用户、自托管的 Agent 指挥中心。v1 只做最小闭环：能稳定聊天、能切换多个 Agent、能派给子 Agent、每次消息落 Run、刷新/重启后能恢复。

#### 用户故事

- 作为用户，我打开网页就能选一个 Agent 开始聊天，回答是流式输出的。
- 作为用户，我可以创建多个 Agent，每个 Agent 有独立的人格提示词、模型和会话。
- 作为用户，主 Agent 可以把一个子任务派给 general-purpose 子 Agent，并能在 Run trace 里看到。
- 作为用户，我刷新页面或重启服务后，会话历史和 Run 状态还在。
- 作为用户，我可以取消一个正在运行的 Run。

#### 功能点清单

1. 单用户 token 登录。
2. Provider 配置：OpenAI-compatible（DeepSeek），可配置 base_url、model、api_key。
3. Agent CRUD：名称、instructions、provider/model、status。
4. 每个 Agent 独立会话列表；新建/切换会话。
5. Chat：流式输出、历史消息、错误展示。
6. 每次用户消息或手动运行创建一个 Run；Run 状态、事件、usage/cost 落盘。
7. 默认 general-purpose 子 Agent；主 Agent 可委派任务；子 Agent 响应进入 Run trace。
8. Checkpoint：每个 session 一个 thread；刷新/重启后可继续同一会话。
9. 取消 Run：queued/running 都能取消并落终态。
10. 右侧抽屉：Run 时间线/事件/usage、Agent 配置。
11. 不同 session 的 Run 可并发执行，最多 `BROWSER_CONCURRENCY` 个（默认 2）；同一 session 仍最多一个活动 Run。并发数与浏览器标签页上限绑定，避免多个 Run 叠加出更多 Chromium 实例。
12. Provider 模型调用失败时按可重试错误（429/5xx/超时/连接）做最多 3 次指数退避 + jitter；4xx 立即失败。`ChatOpenAI` 的 SDK 自身重试关闭，避免与中间件叠加。可重试失败写入 warning 日志（agent/session/异常类型）。

#### 验收标准

- 能创建/切换多个 Agent；每个 Agent 的会话、checkpoint 隔离。
- Chat 流式输出；刷新后会话历史与 Run 状态可恢复。
- 主 Agent 能把任务派给 general-purpose 子 Agent；trace 可见。
- 每次用户消息创建 Run；Run 详情展示事件、usage/cost。
- 重启 api/worker 后 queued/running 能恢复：优雅停止和进程崩溃都会把 running 重新排队，启动后继续；用户主动取消的 Run 保持 cancelled，不会被重启复活。仍打开的前端页面刷新后重新接上该 Run。
- 两个不同 session 的 Run 可同时处于 running；第三个 session 的 Run 排队，直到有空位；同一 session 的第二个 Run 仍被拒绝。
- 服务重启时正在运行的 Run 变为 queued 并在启动后继续；用户主动取消的 Run 不会被重启复活；仍打开的前端页面刷新后重新接上该 Run。
- 429/5xx/超时/连接错误会退避重试；401/400 等 4xx 立即失败。
- 取消 Run 能停止并落终态。
- 不暴露文件、Shell、浏览器、skills、memory 工具。
- 后端测试与前端构建通过。

#### 约束边界

- 单用户、自托管；不做多租户/RBAC。
- v1 不接入微信/Telegram、WebDAV、行情、事件/触发器/定时/Webhook。
- v1 不做审批/HITL（无副作用工具）。
- 不做自定义子 Agent、Agent 间协作。
- 不做技能市场、技能版本/eval、向量记忆。
- 旧 workspace 数据不迁移、不双写；`.super-personal-platform/` 仅保留，不作为 v1 数据源。

#### 非目标

- 交互式浏览器、文件系统、Shell、Skills、Memory；
- 移动端 App；
- 独立 Command Center 首页和事件流大盘；
- 旧 API/页面/执行面的兼容层。

#### 开放问题

- v2 先加 Skills/Memory 还是文件/Shell？

### F2 配置页与事件可读性

- 状态：进行中
- 日期：2026-10-19
- 来源：F1 使用反馈

#### 背景

右侧抽屉只覆盖 Run 和 Agent，Provider 与运行环境分散；Run 事件把每个流式片段原样铺开，噪音大；宽屏下消息列左右跨度过宽。

#### 功能点

1. 新增独立配置页：侧栏入口；集中新增/编辑 Provider，编辑 Agent，并查看运行环境。
2. Agent tab 列出全部 Agent 的摘要（Provider/model、启用技能数、自动记忆开关、状态）；点击行切换全局选中的 Agent（与侧边栏选择器同步），下方编辑区只编辑当前 Agent，不持有第二套选择状态。
3. 运行环境展示：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关。
4. Run 事件默认隐藏 `assistant_delta` 原始片段，可展开查看；事件仍完整落库。
5. Chat 内容列统一宽度，标题、消息和输入框对齐。

#### 验收标准

- 配置页能新增/编辑 Provider、编辑 Agent、查看运行环境。
- Agent tab 显示全部 Agent 的摘要；点击非当前 Agent 后，侧边栏选择器与编辑区同步切换，会话列表跟随切换。
- 原始流式片段默认折叠，展开后可见。
- 消息与输入框左右边界对齐，宽屏下不出现过宽横向跨度。
- 后端测试与前端构建通过。

#### 非目标

- Provider 删除、密钥轮换审计；
- 多用户/RBAC；
- 事件保留策略与自动清理。

### F3 生产改为宿主机 systemd + SQLite

- 状态：进行中
- 日期：2026-10-19
- 来源：F2 部署反馈

#### 背景

v1 生产使用 Docker Compose（Postgres + API + Web）。容器层增加了构建、日志、卷备份和端口映射的运维成本；用户希望回到可直接改代码、直接重启的宿主机运行方式。

#### 功能点

1. 生产改为用户级 systemd 服务 + 仓库内 venv，单进程监听 8888。
2. 数据库改为 SQLite，文件位于部署目录 `data/app.db`；checkpoint 在 `data/checkpoints/`。
3. FastAPI 直接托管 `apps/web/dist`，提供 SPA fallback；生产不再使用 nginx。
4. 时间列使用 UTC-aware 类型，SQLite 读回仍带时区。
5. SQLite 开启 WAL 与 busy timeout。
6. 现有 Postgres 数据一次性迁移到 SQLite，保留 Provider、Agent、Session、Message、Run、RunEvent。

#### 验收标准

- 生产只有一个 systemd 服务在 8888 监听，Docker 容器全部停止。
- 原有数据条数与迁移前一致，前端能正常登录、看会话和 Run。
- 刷新/重启服务后会话与 Run 可恢复。
- `/api/health` 200；`/api/system` 返回 sqlite。
- 后端测试与前端构建通过。

#### 非目标

- 多实例/高可用；
- Postgres 与 SQLite 双写；
- 容器回滚路径；
- Agent 工具能力（文件/Shell/浏览器）。

### F4 彻底删除 Agent 与 Chat 体验升级

- 状态：进行中
- 日期：2026-10-19
- 来源：F2/F3 使用反馈

#### 背景

Agent 只能归档，不能彻底删除；Chat 页面是功能骨架，缺少 Markdown、消息操作和产品化的视觉层次。

#### 功能点

1. Agent 彻底删除：级联删除该 Agent 的 sessions、messages、runs、run_events 和 checkpoint；有活动 Run 时拒绝；二次确认要求输入 Agent 名称。
2. Chat 视觉方向 B：浅色画布、浅色侧栏、悬浮输入卡片、assistant 消息行 + user 气泡。
3. Markdown 渲染：用户消息和 assistant 消息都支持 GFM（表格、列表、任务列表）、代码高亮、代码块复制、链接和图片。
4. 消息操作：复制消息、引用到输入框、失败 Run 重试。
5. 交互：流式 Markdown、停止生成、自动增高输入框、靠近底部才自动滚动、空状态建议；详情抽屉默认关闭，关闭时内容列加宽。
6. 新对话懒创建：点击「新对话」只进入空白草稿状态，首次发送消息时才创建 Session。
7. 失败 Run 在刷新后仍展示错误和重试入口。
8. 多 Agent 切换连续性：输入草稿按 Agent 保留（当前页面内）；切换时立即清空旧 Agent 的会话与消息视图，避免在新 Agent 标题下闪现旧内容；并发会话列表请求以最后一次切换为准。
9. 运行态与错误作用域：同一时间只跟踪一个 Run；另一个会话的 Run 运行中时，当前会话禁用发送并提示，输入框仍可编辑；异步失败只在所属 Agent/会话仍是当前视图时显示错误，切回后失败 Run 的错误和重试从 Run 恢复。

#### 验收标准

- 配置页可彻底删除 Agent，删除后会话、Run、事件和 checkpoint 一并消失。
- 有活动 Run 的 Agent 无法删除。
- Chat 为浅色主题；assistant 消息按 Markdown 渲染，代码块可复制。
- 用户消息可复制和引用；失败 Run 可重试。
- 刷新页面后失败 Run 的错误和重试按钮仍然可见。
- 切换 Agent 时输入草稿按 Agent 恢复，旧 Agent 的消息不会在新 Agent 下短暂显示。
- 另一个会话的 Run 运行中时，当前会话发送按钮禁用并显示提示，输入框仍可输入。
- 切换 Agent/会话后，旧视图的异步错误不再显示在新视图；切回旧会话时失败 Run 的错误与重试仍在。
- 后端测试与前端构建通过。

#### 非目标

- 图片/附件上传（下一阶段）；
- 编辑消息、重新生成、删除单条消息（需要 checkpoint 分支/回滚方案）；
- 会话搜索、重命名、导出；
- 移动端导航改造。

### F5 默认 Agent 提示词对齐 Hermes

- 状态：进行中
- 日期：2026-10-19
- 来源：用户反馈默认 Assistant 的 instructions 过于泛化

#### 背景

默认 Agent 的提示词是 “You are a helpful personal assistant. Answer clearly and concisely.”，缺少 Hermes Agent 默认身份中的直接、长度匹配、无 filler、反谄媚和“深度按需”行为规范。

#### 功能点

1. 默认 seed 提示词是中文个人助手说明：介绍自己是个人助手并列出可做的事（对话、工作区文件、子 Agent、记忆注入、会话 checkpoint），行为规范参考 Hermes Agent 的 behavior spec；不宣称 Hermes 身份。
2. 适配 Agent Command Center：明确当前只有 Chat，没有文件、Shell、浏览器、skills、memory；默认用简体中文回复，用户明确要求其他语言时再切换。
3. 生产已有默认 Agent 一次性更新 instructions，不改名称、Provider、会话或 checkpoint。

#### 验收标准

- 新装环境的默认 Assistant 使用新提示词。
- 生产 `default` Agent 的 instructions 更新为新版本，其他 Agent 不受影响。
- 后端测试与前端构建通过。

#### 非目标

- 不引入 Hermes 的 SOUL.md 体系、记忆、工具或平台提示；
- 不改变模型、Provider、Agent 数量或会话数据。

### F6 长期记忆（user + agent）

- 状态：进行中
- 日期：2026-10-19
- 来源：F5 后的记忆设计讨论

#### 功能点

1. `memory_items` 表，`scope=user|agent`；不设 status，写入即生效，删除即删除。
2. user memory 对所有 Agent 生效；agent memory 只对指定 Agent 生效；session 工作记忆继续由 checkpoint 负责。
3. 配置页独立「记忆」页面：用户/Agent 两个 tab，支持新增、编辑、删除、搜索。
4. 对话里以“记住……”开头的内容，立即写入 user memory。
5. 每个 Agent 有「自动学习记忆」开关；Run 完成后先用 FTS5（trigram）召回相关历史记忆，再让模型返回 add/update/delete，合并或删除旧记忆，最多 12 个操作；带来源、去重、200 条上限。提取在 Run 落 completed 之后作为后台任务执行，不占用 Run 并发槽；同一 Agent 的提取在进程内串行，后一次能看到前一次刚写入的记忆，避免并发写入语义重复但措辞不同的记忆。
6. 每次 Run 按关键词/标签/最近使用取 Top N（最多 12 条、约 1500 字）注入系统提示词，标注为数据而非指令；记录 `run_memories` 用量。
7. 记忆内容不自动修改 Agent instructions。
8. 记忆页面提供「整理记忆」按钮：对当前 scope 的记忆分批交给模型，只允许 update/delete，合并重复、删除失效项，不新增事实。

#### 验收标准

- 记忆页面可管理 user/agent 两种记忆，并按 Agent 隔离。
- 说“记住：……”后用户记忆立即可见。
- 开启自动学习的 Agent 在 Run 完成后能新增 Agent 记忆；关闭后不再提取。
- 下一次 Run 能注入相关记忆；删除记忆后不再注入。
- 后端测试与前端构建通过。

#### 非目标

- 向量/语义检索、embedding；
- Agent 自主修改 instructions；
- user memory 自动提取；
- 记忆审核状态、版本历史或自动遗忘；
- 跨设备同步。

### F7 移除 Docker 部署路径

- 状态：已完成
- 日期：2026-10-19
- 来源：用户确认 Docker 部署已废弃

#### 功能点

1. 删除 `deploy/docker-compose.yml`、`deploy/Dockerfile.api`、`deploy/Dockerfile.web`、`deploy/nginx.conf`；`deploy/` 只保留 systemd unit 与 `.env` 模板。
2. 移除仅为 Postgres 服务的 `asyncpg` 依赖。
3. 同步 README、CLAUDE.md 与 project-commit 技能：生产只保留 systemd + venv + SQLite。

#### 验收标准

- 仓库内不再有 Docker、nginx 或 Postgres 的部署文件与依赖。
- 文档不再把 Docker 描述为可选运行方式。
- 后端测试与前端构建通过。

#### 非目标

- 恢复容器化部署；
- 改动 SQLite 数据或部署目录结构。


### F8 Agent Workspace（真实目录 + 文件工具）

- 状态：进行中
- 日期：2026-10-19
- 来源：workspace 设计讨论

#### 功能点

1. 每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/` 作为工作区，不支持自定义目录、工作区模式或路径配置。
2. 工作区根目录可用 `APP_WORKSPACES_DIR` 覆盖；目录在 Agent 创建时自动创建，已有 Agent 在服务启动时补齐。
3. 工作区只在配置页 Agent 编辑区只读展示，跟随左上角选中的 Agent；不在侧边栏或 API 提供工作区选择。
4. 执行器使用 `FilesystemBackend(virtual_mode=True)` 作为 backend，并通过默认 `FilesystemMiddleware` 向 Agent 开放工作区文件工具（ls / read_file / write_file / edit_file / delete / glob / grep）。
5. 删除 Agent 不删除 workspace 目录；不开放 shell / browser / skills。

#### 验收标准

- 配置页显示当前 Agent 的机器目录 `workspaces/<agent_id>`，切换左上角 Agent 时跟随变化。
- 新建 Agent 后对应目录自动存在；Agent API 不暴露 workspace 配置字段。
- Agent 能在工作区内列出、读取、创建和修改文件，无法越出工作区根目录。
- 后端测试与前端构建通过。

#### 非目标

- 自定义工作区路径或多工作区；
- 每 Agent 的 shell / skills / 细粒度工具开关（后续需求）；
- deepagents 升级；
- 多用户或跨机器 workspace 隔离；
- 删除 Agent 时级联删除 workspace 文件。

### F9 统一 home 数据目录

- 状态：进行中
- 日期：2026-10-19
- 来源：workspace 讨论中的目录统一要求

#### 功能点

1. 运行时目录统一到 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：配置 `.env`、数据库 `data/app.db`、checkpoint `data/checkpoints/`、工作区 `workspaces/`。
2. 配置读取顺序：`$APP_HOME/.env` → 启动目录 `.env`（后者覆盖）；生产继续由 systemd `EnvironmentFile` 注入。
3. systemd unit 改为 home 下的环境文件与数据库/数据目录。
4. 启用前清空旧 `~/.super-personal-platform` 历史数据；生产按“停服务 → 备份 → 迁移 data 与 .env → 更新 unit → 启动验证”执行。

#### 验收标准

- 新环境首次启动自动创建 `data/`、`data/checkpoints/`、`workspaces/`。
- 生产迁移后会话、消息、Run 与记忆数量与迁移前一致；`/api/health` 200，`/api/system` 返回 sqlite。
- 后端测试与前端构建通过。

#### 非目标

- 迁移或保留旧 `.super-personal-platform` 历史数据；
- 多实例 / 多用户目录隔离；
- 改变 workspace 的虚拟 / 真实目录语义。

### F10 Skill 中间件（技能/工具库 + 每 Agent 启用 + 技能工具解锁）

- 状态：进行中
- 日期：2026-10-19
- 来源：用户要求接入 skill middleware，做到“用 skill 才解锁 tool”

#### 功能点

1. 全局技能库：技能放在 `~/.super-personal-platform/skills/<name>/SKILL.md`；`APP_SKILLS_DIR` 可覆盖。技能通过文件系统维护，不做 CRUD 表单；用户可以让 Agent 在对话中创建/编辑。内置技能在启动时 seed（`web-search`、`web-fetch`、`web-browser`），并在技能库根目录的 `.seed_state.json` 记录写入内容的哈希：内容未被用户修改的内置技能随版本更新，用户改过的保持不动。
2. 每个 Agent 有 `enabled_skills` 启用列表；侧边栏左上角的「技能」页面按当前 Agent 勾选启用并立即保存，系统提示只列出当前 Agent 启用的技能。不新增 Agent 选择器。「工具」页面只展示注册/声明情况，不单独启停工具。
3. FileMiddleware 提供的文件工具（ls / read_file / write_file / edit_file / delete / glob / grep）始终可用，不参与解锁，也不需要启用。
4. 技能工具来自平台代码注册表（`apps/api/app/services/skill_tools.py`）；`SKILL.md` 的 `metadata.include_tools` 是空格分隔的工具名。工具必须同时满足：注册表存在、被已启用技能声明、SKILL.md 已读取。
5. 注册表提供两个联网工具：
   - `web_search`：调用 DeepSeek Search API（`POST {base_url}/v1/tools/search`），请求 `query`（1-128 字）、`count`（1-50）、`need_content`，返回标题、链接、摘要、来源和权威性评分。
   - `web_fetch`：调用 DeepSeek Fetch API（`POST {base_url}/v1/tools/fetch`），请求 `{url}`，返回 `{title, content}`（Markdown）。
   两者均使用 Agent Provider 的 key，按成功请求计费，受 DeepSeek QPS 限制。
6. 模型必须先用 `read_file` 读取 `/skills/<name>/SKILL.md`，该技能声明的已启用工具才会在当次模型调用中披露并允许执行；读取之前工具不可见，调用会失败。
7. 解锁在会话内保持：读取记录随 checkpoint 保留，只要技能读取还在上下文中就可用；被摘要压缩掉后自动重新锁定。
8. 每次 Run 重新扫描技能目录并应用最新的技能启用列表。
9. Agent 可以在对话中通过文件工具创建/修改 `/skills/<name>/SKILL.md`，不需要表单；技能声明不是安全边界，工具是否可用由每 Agent 启用的技能控制。
10. `GET /api/skills` 返回扫描到的技能与解析错误；「技能」页面展示并启停技能。
11. `GET /api/tools` 返回文件工具与技能工具；「工具」页面展示文件工具（始终可用）、技能工具的注册状态和声明技能，不提供工具级启停。

#### 验收标准

- 左侧「技能」页面能看到全局技能库，并为当前 Agent 勾选/取消技能；保存后下一轮 Run 生效。
- 左侧「工具」页面能看到文件工具与技能工具的注册/声明情况；工具不单独启停。
- 未启用的技能不出现在系统提示中，读取它的 SKILL.md 不会解锁工具。
- 技能工具在读取 SKILL.md 前不可见且不可调用，读取后在同一会话内可用。
- `web_search` 和 `web_fetch` 能按 DeepSeek Search/Fetch 协议返回结构化结果与页面正文；错误码能回传给模型。
- 用户可以让 Agent 在对话中创建/修改技能；创建后能在「技能」页面为当前 Agent 启用。
- Agent 无法绕过技能启用限制：只有已启用技能声明的已注册工具才可能解锁。
- 后端测试与前端构建通过。

#### 非目标

- 技能 CRUD UI/API、技能市场、版本管理、eval；
- 技能目录内的 Python 插件动态加载；
- 每 session 的技能/工具启用、工具级细粒度权限；
- 禁用技能的读取拦截（只做提示过滤与解锁门控）；
- Shell / 浏览器工具、多用户隔离、技能沙箱。

### F11 Playwright 无头浏览器工具（技能门控）

- 状态：进行中
- 日期：2026-10-20
- 来源：用户确认浏览器并发数 2、保留 web_fetch/web_search；截图仅在用户明确要求时返回

#### 功能点

1. 新增 seed 技能 `web-browser`，声明并解锁 7 个浏览器工具：`browser_open`、`browser_snapshot`、`browser_click`、`browser_fill`、`browser_press`、`browser_screenshot`、`browser_close`；沿用 F10 的「已注册 + 被已启用技能声明 + SKILL.md 已读取」三条件。
2. 每次 Run 懒启动一个无头 Chromium 和一个浏览器 context；同一 Run 最多 `BROWSER_CONCURRENCY=2` 个标签页，工具用 `tab` 参数区分，默认 `main`；Run 结束、失败、超时或取消时自动关闭。
3. 默认只返回页面文字：标题、URL、可见正文和可交互元素 ref；ref 只对最近一次快照有效，动作后返回新快照。
4. 支持打开网页、读取快照、点击、填写（含 select）、按键和关闭标签页。
5. `browser_screenshot` 按需返回当前页面的 PNG 图片（可整页），仅在用户明确要求查看截图/外观/布局时调用；Provider 模型必须支持图片输入，图片大小受 `BROWSER_SCREENSHOT_MAX_BYTES` 限制。
6. 安全边界：只允许公开 http/https 地址，拒绝 localhost、内网/回环/链路本地/保留地址、带凭据 URL、`file://` 和下载；导航请求进入页面前做 DNS 校验。
7. Playwright 或 Chromium 不可用时，工具向模型返回可读错误并提示安装命令，不中断 Run。
8. Run 事件新增 `browser_action`（工具名 + 紧凑摘要），前端抽屉可读展示；截图内容不写入事件。
9. `BROWSER_*` 配置控制标签页上限、导航/动作超时、快照元素与文字上限、截图大小上限，以及 Chromium sandbox 逃生开关。
10. 搜索类浏览优先尝试移动版/轻量入口；遇到验证码、安全验证、HTTP 403/429 或其他拦截页时，工具返回明确错误并提示换入口或交给用户，不尝试绕过验证。

#### 验收标准

- 「技能」页面能启用 `web-browser`；未读取 SKILL.md 前工具不可见且不可调用。
- Agent 能打开 example.com，读取标题与正文，点击链接后拿到新快照，并用 `browser_close` 释放标签页。
- 用户明确要求截图时，`browser_screenshot` 返回 PNG 图片内容块；未要求时不自动截图。
- 同一 Run 打开第 3 个标签页返回上限错误，不阻塞其他工具调用。
- `http://127.0.0.1`、`http://localhost`、`http://169.254.169.254`、`file:///etc/passwd`、带凭据 URL 均返回 `Error:` 且不发生导航。
- 遇到验证码、安全验证或 HTTP 403/429 时返回明确错误并提示换入口，不自动尝试绕过。
- Run 完成、失败或取消后没有残留 Chromium 进程。
- 后端测试与前端构建通过。

#### 非目标

- 自动截图或持续视觉输入（截图只由用户明确要求触发）；
- 验证码识别或绕过、反检测/stealth；
- iframe/shadow DOM 穿透、弹窗接管；
- 持久登录态、Cookie/Profile 管理、下载/上传、任意 JS 执行；
- 反检测/stealth、代理配置、用浏览器替代 web_search；
- 多用户隔离、每 session 的浏览器状态。

### F12 主题统一与移动端适配

- 状态：进行中
- 日期：2026-10-20
- 来源：用户要求前端视觉更贴合既定主题，并适配移动端

#### 背景

界面在窄屏下直接隐藏侧栏和详情抽屉，手机上无法切换 Agent、会话和页面；颜色、间距与控件状态存在零散硬编码，观感与“浅色画布 + 悬浮输入卡片”的既定方向不够统一。

#### 功能点

1. 统一视觉基线：色板、状态色、阴影、圆角与焦点态收敛为设计 token；保持浅色暖底、青绿主色、assistant 消息 Markdown 和悬浮输入卡片。
2. Chat 增加轻量顶栏：显示当前 Agent 和会话标题，并作为详情抽屉的开关；移除原浮动按钮。
3. 移动端（≤860px）使用顶部栏 + 汉堡菜单：侧栏变为带遮罩的抽屉，切换会话、Agent 或页面后自动收起；详情抽屉变为全屏面板。
4. 输入区适配软键盘与安全区：跟随 `visualViewport` 收缩，底部使用 safe-area；移动端输入字号不小于 16px。
5. 配置、记忆、技能、工具页面在移动端改为单列卡片，标签页等宽，弹窗改为底部面板；表格和代码块在自身容器内横向滚动。
6. 支持 `prefers-reduced-motion`。
7. assistant 消息使用左侧气泡卡片，与右侧 user 气泡形成左右对话；窄屏下气泡不超过内容列。
8. 输入卡片按内容自适应：单行时「＋」菜单在左、输入居中、发送在右；换行或多行时输入在上，菜单与发送位于底部两侧。
9. 会话删除、记忆删除和整理记忆使用应用内确认弹窗，不使用浏览器原生 confirm。

#### 验收标准

- 桌面端保持侧栏 + 对话 + 详情三栏；Chat 顶栏可打开/收起详情。
- ≤860px 时可通过汉堡菜单访问 Agent、会话、技能、工具、记忆、配置和退出登录；选中后抽屉收起。
- 手机视口下消息、表格、代码块不产生页面级横向滚动；软键盘弹起时输入框保持可见。
- 后端测试与前端构建通过。

#### 非目标

- 移动端原生 App、PWA 离线能力；
- 深色主题或主题切换；
- 手势（滑动返回、滑动关闭抽屉）；
- 重做品牌视觉与插画。

### F13 输入区附件上传（保存到工作区）

- 状态：进行中
- 日期：2026-10-20
- 来源：用户要求输入区左侧改为「＋」菜单，包含上传附件

#### 功能点

1. Chat 输入卡片左侧为「＋」菜单：上传附件、清空输入；单行/多行布局沿用 F12。
2. 附件通过 `POST /api/agents/{agent_id}/attachments?filename=...` 以原始字节上传，保存到 `workspaces/<agent_id>/uploads/`，单文件上限 10 MB；文件名去掉路径和控制字符，加随机前缀防冲突。
3. 上传成功后在输入卡片内显示文件名 chip，可单独移除；发送时把文件名和工作区路径作为附件清单写入用户消息，Agent 用文件工具读取。
4. 附件不写入数据库、不进入 checkpoint，只作为当前 Agent 工作区里的文件存在。
5. 发送成功后清空附件 chip；发送失败保留草稿与已上传附件。
6. 不做图片理解或文档解析；图片、PDF 等二进制文件只保存，能否读取取决于模型能力和文件工具。

#### 验收标准

- 「＋」菜单可上传文件；工作区出现对应文件，发送后的用户消息包含文件名与路径。
- 空文件、超过 10 MB、未知 Agent 返回明确错误；文件名中的路径穿越被拒绝。
- 单行/多行输入布局正常，移动端菜单不溢出。
- 后端测试与前端构建通过。

#### 非目标

- 图片理解、PDF 解析、OCR、向量化；
- 附件元数据入库、历史附件列表、下载入口；
- 拖拽上传、粘贴图片、多用户配额。

### F14 技能查看与编辑（现有 SKILL.md）

- 状态：进行中
- 日期：2026-10-20
- 来源：用户确认技能应能在界面中查看和编辑

#### 功能点

1. `GET /api/skills/{directory}` 返回 SKILL.md 全文、路径和解析警告；`PUT` 保存编辑后的全文。
2. 「技能」页面每个技能提供「查看」入口，打开模态编辑器；可直接编辑 SKILL.md 并保存，保存后重新扫描技能库。
3. 保存前校验 frontmatter：必须包含 name 和 description，YAML 可解析；写入使用临时文件原子替换；单个文件上限 10 MB。
4. 只允许编辑技能根目录下的现有 `<directory>/SKILL.md`；拒绝路径穿越、隐藏目录和符号链接。
5. 新建技能仍通过对话让 Agent 创建，不提供创建表单；编辑器只修改现有文件。
6. 技能启用状态不受编辑影响；保存后的 description 和工具声明在下一轮 Run 生效。

#### 验收标准

- 技能页可打开任一技能查看全文并保存修改；磁盘文件更新，列表描述刷新。
- 非法 frontmatter 返回 422 且不覆盖原文件；未知技能 404；未登录 401。
- 移动端编辑器可用。
- 后端测试与前端构建通过。

#### 非目标

- 技能创建表单、重命名/删除技能、版本历史或 diff；
- 多人并发编辑与冲突合并；
- 编辑器语法高亮或 Markdown 预览。
