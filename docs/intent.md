# Intent

- 状态：现行
- 最后更新：2026-10-19
- 编号水位：F6

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

#### 验收标准

- 能创建/切换多个 Agent；每个 Agent 的会话、checkpoint 隔离。
- Chat 流式输出；刷新后会话历史与 Run 状态可恢复。
- 主 Agent 能把任务派给 general-purpose 子 Agent；trace 可见。
- 每次用户消息创建 Run；Run 详情展示事件、usage/cost。
- 重启 api/worker 后 queued/running 能恢复；同一 session 可继续。
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
2. 运行环境展示：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关。
3. Run 事件默认隐藏 `assistant_delta` 原始片段，可展开查看；事件仍完整落库。
4. Chat 内容列统一宽度，标题、消息和输入框对齐。

#### 验收标准

- 配置页能新增/编辑 Provider、编辑 Agent、查看运行环境。
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
6. 失败 Run 在刷新后仍展示错误和重试入口。

#### 验收标准

- 配置页可彻底删除 Agent，删除后会话、Run、事件和 checkpoint 一并消失。
- 有活动 Run 的 Agent 无法删除。
- Chat 为浅色主题；assistant 消息按 Markdown 渲染，代码块可复制。
- 用户消息可复制和引用；失败 Run 可重试。
- 刷新页面后失败 Run 的错误和重试按钮仍然可见。
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

1. 默认 seed 提示词参考 Hermes Agent 的默认身份 behavior spec，并用中文书写。
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
5. 每个 Agent 有「自动学习记忆」开关；Run 完成后先用 FTS5（trigram）召回相关历史记忆，再让模型返回 add/update/delete，合并或删除旧记忆，最多 3 个操作；带来源、去重、200 条上限。
6. 每次 Run 按关键词/标签/最近使用取 Top N（最多 12 条、约 1500 字）注入系统提示词，标注为数据而非指令；记录 `run_memories` 用量。
7. 记忆内容不自动修改 Agent instructions。

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

