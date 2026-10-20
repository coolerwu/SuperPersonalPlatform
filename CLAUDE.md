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
- Provider 模型调用使用 langchain `ModelRetryMiddleware`：429/5xx/超时/连接错误最多重试 3 次（指数退避 + jitter），默认按 `ModelError.is_retryable` 判定，4xx 不重试；`ChatOpenAI` 的 SDK 自身重试关闭，避免叠加。可重试失败会记录 warning 日志（agent/session/异常类型）。
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`，没有 nginx/Postgres 容器。
- v1 只做 Chat、多 Agent、默认 general-purpose Subagent、Checkpoint/Runs、集中配置页和长期记忆。
- v1 暴露工作区内的 filesystem 工具（ls / read_file / write_file / edit_file / delete / glob / grep，由 `FilesystemMiddleware` 提供）；不暴露 shell 工具；浏览器工具通过 `web-browser` 技能门控解锁（Playwright 无头 Chromium，默认文本快照，最多 2 个标签页，可按需返回截图）。memory 由平台管理和注入，不允许 Agent 自主修改 instructions。
- Skills/Tools：全局技能库 `~/.super-personal-platform/skills/<name>/SKILL.md`（`APP_SKILLS_DIR` 可覆盖）+ 每个 Agent 的 `enabled_skills` 启用列表。系统提示只列出启用技能；模型用 `read_file` 读取 `/skills/<name>/SKILL.md` 后，技能在 `metadata.include_tools` 中声明的已注册工具才解锁，读取前不可见也不可调用；文件工具始终可用。技能工具来自代码注册表 `apps/api/app/services/skill_tools.py`（`web_search` / `web_fetch`，DeepSeek Search/Fetch API，按成功请求计费）和 `apps/api/app/services/browser_tools.py`（`browser_open` / `browser_snapshot` / `browser_click` / `browser_fill` / `browser_press` / `browser_screenshot` / `browser_close`），不在技能目录执行 Python。Agent 可以在对话中直接用文件工具创建或修改 `/skills/**` 下的技能，不需要表单；控制点是当前 Agent 启用了哪些技能，启用技能即允许它声明的已注册工具；技能声明本身不是安全边界。首次启动 seed `web-search`、`web-fetch` 和 `web-browser` 技能；内置技能的写入哈希记录在 `~/.super-personal-platform/skills/.seed_state.json`，启动时只有内容与记录一致（用户未修改）的内置技能才会随版本更新，用户改过的保持不动；无记录的文件不动，直到其内容与内置默认一致后开始跟踪。「技能」页面可以打开现有 SKILL.md 查看并直接编辑保存（原子写入，保存前校验 frontmatter）；新建技能仍通过对话完成。
- Browser：每次 Run 懒启动一个 Chromium + context，`BROWSER_CONCURRENCY`（默认 2）限制标签页；执行器在 `finally` 关闭（含失败、超时、取消）；只允许公开 http/https，拒绝 localhost、私网、`file://` 和下载；默认返回文字和元素 ref，只有用户明确要求时才用 `browser_screenshot` 返回 PNG 图片（需要 Provider 支持图片输入）；搜索优先尝试移动/轻量入口，遇到验证码或 HTTP 403/429 返回明确错误并提示换入口，不尝试绕过；相关配置为 `BROWSER_*`。
- 记忆：`memory_items`（scope=user|agent，无 status）+ `run_memories`；user memory 全局、agent memory 按 Agent 隔离；session 工作记忆仍由 checkpoint 负责。
- 写入：配置页手动 CRUD、对话里“记住……”写 user memory、每个 Agent 的 `auto_memory` 开关在 Run 完成后提取 agent memory；先用 FTS5（trigram）找相关历史记忆，再让模型返回 add/update/delete 做合并，最多 12 个操作、200 条上限。提取在 Run 落 completed 后作为后台任务执行，不占用 Run 并发槽；同一 Agent 的提取在进程内串行（单进程部署），后一次能看到前一次刚写入的记忆，避免并发写入语义重复的条目。
- 读取：每次 Run 按关键词/标签/最近使用取 Top N（最多 12 条、约 1500 字）注入，标注为数据而非指令；记录本次使用的 memory id。
- 记忆页面提供「整理记忆」：分批让模型对现有记忆做 update/delete，只合并不新增。
- Workspace：每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/`（`APP_WORKSPACES_DIR` 可覆盖），不支持自定义路径；配置页只读展示机器目录，并跟随左上角选中的 Agent。执行器用 `FilesystemBackend(virtual_mode=True)` + 默认 `FilesystemMiddleware` 开放工作区文件工具。
- 运行时目录统一在 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：`.env`、`data/app.db`、`data/checkpoints/`、`workspaces/`、`skills/`。
- 默认 Assistant 使用中文个人助手提示词：先说明自己是个人助手，并列出可做的对话、工作区文件、general-purpose 子 Agent、记忆注入、会话 checkpoint 和技能（读取 SKILL.md 后解锁技能工具）；行为规范参考 Hermes（直接、长度匹配问题、无 filler、反谄媚、深度按需），默认简体中文回复，不宣称 Hermes 身份。新建 Agent 未填写 instructions 时使用同一套默认提示词；运行时不使用任何兜底提示词或兜底 Agent 名称。
- UI 视觉方向：明亮简洁（浅色画布 + 悬浮输入卡片），assistant 消息按 Markdown 渲染；依赖 `react-markdown`、`remark-gfm`、`rehype-highlight`。Chat 顶部有轻量栏（Agent/会话标题 + 详情开关）；≤860px 使用顶部栏 + 汉堡菜单，侧栏变为抽屉、详情变为全屏面板，输入区跟随 `visualViewport` 与安全区收缩。
- 输入区：左侧「＋」菜单提供「上传附件」和「清空输入」。附件保存到 `workspaces/<agent_id>/uploads/`，单文件上限 10 MB；发送时把文件名与工作区路径写入用户消息，Agent 用文件工具读取，附件不写入数据库。
- 消息操作：复制、引用、失败 Run 重试；不做编辑/重新生成/删除单条消息（需要 checkpoint 分支方案）。
- Agent 支持彻底删除 `DELETE /api/agents/{id}/permanent`，级联 sessions/messages/runs/run_events/checkpoints；有活动 Run 时 409。
- 配置页集中管理 Provider（查看/新增/编辑）、Agent 和运行环境；另有独立的「记忆」页面管理 user/agent 记忆。`GET /api/system` 只返回非敏感信息。
- 所有 per-agent 配置都以左上角侧边栏的 Agent 状态为唯一来源；配置页的 Agent tab 列出全部 Agent 摘要（Provider/model、启用技能数、自动记忆、状态），点击行即切换这个全局选中的 Agent，侧边栏同步；下方编辑区只编辑当前 Agent，不持有第二套选择状态。新增 per-agent 配置时必须沿用这一规则。侧边栏左上角另有独立的「技能」「工具」页面：技能启停按当前 Agent 立即保存；工具页只展示文件工具与技能工具的注册/声明情况，不单独启停工具。
- Chat 内容列统一宽度：header、消息流、composer 都以 `--chat-lane` 对齐。

## 运行约定

- 每次用户消息或手动运行创建一个 Run；状态：`queued / running / completed / failed / cancelled`。
- Session 懒创建：点击「新对话」只清空视图，首次发送时才创建 Session 和 Run；不要为了展示空状态提前落库。
- 同一 session 最多一个活动 Run；不同 session 的 Run 可并发执行，最多 `BROWSER_CONCURRENCY` 个（默认 2）。Run 并发数与浏览器标签页上限绑定，避免多个 Run 叠加出更多 Chromium 实例。
- 服务停止/重启时，正在执行的 Run 重新入队（清空 `started_at`）并在启动后继续；已请求取消的 Run 落 `cancelled`，不会被重启复活。启动时 `requeue_running_runs` 兜底崩溃留下的 `running`。重新入队不是事务回滚：已经完成的文件/浏览器副作用不会撤销。
- Run 事件 append-only，存 `run_events` 表；其中 `assistant_delta` 是每个流式片段一行，SSE/轮询和刷新恢复依赖它。前端默认折叠原始片段，仅保留 Run/工具/子 Agent/用量等可读事件。
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
