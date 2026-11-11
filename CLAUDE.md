# CLAUDE.md

本文件是新项目 `Agent Command Center` 的协作约定。默认使用中文回复用户。

## 需求与方案

- `docs/intent.md` 是需求唯一来源（F1–F17，2026-11-11 按 SDK 迁移后的实现重写）；没有它覆盖的改动不进入实现。
- 方案先定后做；涉及新依赖、破坏性接口、数据迁移或 schema 变更时，动手前先列出并等确认。
- 每次修改代码后，按用户要求做 3 次自我 code review，并如实标注发现。
- 迁移设计与计划见 `docs/superpowers/specs/2026-11-10-claude-agent-sdk-migration-design.md` 与 `docs/superpowers/plans/`。

## 当前架构

- v1 从 0 到 1 重建；`apps/api` 使用 FastAPI + Claude Agent SDK（Python，`ClaudeSDKClient`），模型走 DeepSeek 的 Anthropic 兼容端点；`apps/web` 使用 React + Vite + TypeScript。
- 执行核：`SessionRegistry`（按 session 串行、跨 session 并发，并发数 = `BROWSER_CONCURRENCY`）+ `ClaudeTurnRunner`。缓存 client 按 `(agent_id, session_id)` 区分，默认空闲 TTL 300 秒、上限 8 个、每 60 秒清扫；轮末逐出在并发信号量释放后执行，断开有超时；`close_all` 每个 client 限时断开并把超时对象保留在 `_orphaned_clients` 并记 warning。
- 会话/消息由 SDK transcript 管理：`~/.super-personal-platform/claude/projects/<agent_id>/<uuid>.jsonl`；会话列表/历史/重命名/删除走 `sdk_bridge` / `session_catalog`，平台不建 sessions/messages/runs 表。
- 平台配置在 `data/settings.json`（`runtime`、`knowledge`、`agents`、`channels`、`channel_conversations`）；单文件、单锁、临时文件 + fsync + `os.replace`，写前留 `.bak`；密钥字段只返回 `has_api_key` / `has_password` / `has_credentials`。`.env` 密钥仅在首次种子时复制到 `settings.json`，之后以文件为准，平台不回写 `.env`。
- 新库的 `app.db` 只创建六张表：`audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`；从旧版升级的库会保留未使用的旧表。`market.db` 行情缓存不变。
- `instructions` 通过 SDK `system_prompt={type: preset, preset: claude_code, append: instructions}` 注入；修改 Agent/Provider 后关闭该 Agent 的空闲缓存 client；忙碌会话在下一次 turn 开始时按配置指纹（instructions + provider base_url/model/api_key）检测并重建。
- 重启恢复：`running_sessions` 租约 + `resume`；恢复提示要求先检查状态、继续原任务、不重复已完成步骤；没有 transcript 的租约删除，但渠道空投递行填入「服务重启时这条消息尚未开始处理，请重新发送」并交由启动补发，网页来源写 `audit_log`；暂停/归档 Agent 的租约保留。
- Agent 状态：`active` 正常接收网页/渠道/定时；`paused` 与 `archived` 都拒绝网页新消息（409）、暂停该 Agent 的渠道轮询（保留 cursor）并跳过定时任务；非 active Agent 的渠道仍可在渠道页查看、编辑和删除；配置页把状态改回 `active` 即恢复（archived 也在 `GET /api/agents` 列表中），微信轮询随状态变更自动重启。
- 网页聊天：`POST /api/chat/messages` → `{session_id, turn_id, accepted}`；SSE `GET /api/sessions/{uuid}/stream?turn_id=...`；历史 `GET /api/sessions/{uuid}/messages?agent_id=...`；中断 `POST /api/sessions/{uuid}/interrupt`；命令 `POST /api/chat/commands`（`/new` `/clear` `/stop` `/status` `/help`）。网页提交校验 Agent 为 active 且 session 属于该 Agent。同进程刷新会在数秒内通过 `current_turn_id` 重放当前轮缓冲；进程重启或断线才丢失，历史从 transcript 重建。
- 渠道：配置与 `channel_conversations`（key `"{channel_id}:{external_id}"`，含 `context_token`）在 `settings.json`；`channel_inbounds` 幂等去重；`channel_deliveries` 在 turn 开始写 pending、结束回填并发送，重启补发；同一联系人 5 秒打包；文本/图片/文件、引用找回与渠道命令都保留；运行期截图实时推送等工具层接入（当前 `channel_images` 无调用方）。
- 定时：`cron_jobs.session_id` 是 SDK 会话 UUID；每次触发在专属会话入队一个 turn；会话忙时跳过并写 `audit_log`；启动补跑最多一次；推送目标是当前 Agent 的 active 微信渠道下已有 1:1 联系人，复用渠道投递。
- 清理：`SessionCleanupRuntime` 按 transcript mtime 与 `runtime.session_retention_days`（默认 30，0 关闭）删除；删除前用 registry 的 `begin_delete` 原子标记，跳过租约/忙碌/删除中的会话；后台清理先清产物（截图、投递、渠道绑定）再删 transcript，手动删除会话先调用 SDK `delete_session` 再尽力清理产物；`claude/` 下 auto memory 保留。
- 记忆：用户画像 = `claude/CLAUDE.md`（记忆页单文档编辑，保存留 `.bak`；文件 mtime/size 纳入 client 配置指纹，下一次 turn 重建受影响的缓存 client）；Agent 指令在 `settings.json`；Agent 笔记由 Claude auto memory 管理；`agents[].auto_memory` 仅为兼容保留，运行时不再读取。平台没有记忆表、记忆工具、`/memory` 指令或整理记忆。
- 技能：全局 `claude/skills/<name>/SKILL.md`；`DEFAULT_SKILLS` 为 `web-browser`、`knowledge-base`、`quant-backtest`、`cron-scheduler`、`trade-journal`、`allocation`、`chanlun`（网页搜索/抓取改用 Claude Code 自带的 WebSearch/WebFetch）；`.seed_state.json` 记录内置内容哈希，只更新用户未修改的内置技能；已从默认列表移除且未修改的技能只删除被跟踪的 SKILL.md，保留用户附加文件；技能没有启用开关，所有 Agent 自动共享；平台技能工具尚未接入 SDK。
- 插件集市：「插件」页（`/plugins`）管理 Claude Code 官方集市 `anthropics/claude-plugins-official`；插件以 user scope 装进运行时 `claude/`（`settings.json` 的 `enabledPlugins`、`plugins/`），所有 Agent 共享。平台调用 SDK 内置 CLI（与 runner 同一二进制）执行 `plugin` 命令，启动与读取不联网；`settings.json`、`plugins/installed_plugins.json` 与 `.plugins-revision` 纳入 client 配置指纹，安装/启停/更新/卸载后下一轮对话重建。插件可带 hooks/MCP，平台不提供沙箱。
- 工具层未迁移：平台注册表中的技能工具、`add_dirs`、MCP、搜索/浏览器/回测/定时/交易/资产/缠论/知识库工具都没有接入 SDK；WebFetch/WebSearch 等只是 Claude Code 默认工具集的一部分。runner 未显式设置 `tools`，默认工具集随 bundled CLI 版本和插件环境变化，以工具页/运行时 init frame 为准（例如 Read/Write/Edit/Bash/Task/WebFetch/WebSearch/Skill）。工具页按 API 展示，平台与技能工具标记为未接入。`langchain-core` 暂留。
- 知识库：`KnowledgeSyncRuntime` 在进程内把 WebDAV 目录双向同步到 `knowledge/`；配置在 `settings.json` 的 `knowledge` 节；本版不挂载给 Agent。
- 业务页面（回测、交易记录、资产配置、缠论图）不绑定 Agent，逻辑与旧版一致：`backtests` 表、`trade-journal.jsonl`、`allocation.json` + `allocation-ledger.jsonl`、`market.db`。
- Agent 工作区固定 `workspaces/<agent_id>/`；归档保留工作区，彻底删除会删除工作区与 transcript 目录。
- 运行时目录 `~/.super-personal-platform/`：`claude/`（含 `skills/` 技能库、`plugins/` 集市与插件、`settings.json`、`CLAUDE.md` 画像）、`workspaces/<agent>/`、`knowledge/`、`tmp/`、`data/{settings.json, app.db, market.db, knowledge_sync_state.json, trade-journal.jsonl, allocation.json, allocation-ledger.jsonl, browser_state.json, backtests/}`、`.env`。
- 生产：宿主机 systemd 用户服务 + venv + SQLite，单进程 8888；FastAPI 直接托管 `apps/web/dist`。
- UI 沿用珍珠冷光 + 极光 + 玻璃风格；路由为自建 History API（`apps/web/src/routes.ts`），页面 `/chat`、`/config`、`/memory`、`/skills`、`/tools`、`/plugins`、`/channels`、`/cron`、`/backtest`、`/journal`、`/allocation`、`/chan`；≤860px 顶部栏 + 抽屉。

## 运行约定

- 同一 session 串行、不同 session 并发；网页、微信、定时统一进入 `SessionRegistry`。
- 网页发送在运行中排队；`/stop` 先取消排队 turn，再打断正在执行的 turn。
- 前端按 session 保存流式状态；同进程刷新会在数秒内通过 `current_turn_id` 重放当前轮缓冲，进程重启/断线才丢失；历史从 transcript 读回；不显示消息时间戳。
- 附件保存到 `workspaces/<agent_id>/uploads/`，单文件 10 MB；图片浏览器端缩到最长边 1600 px；消息里写文件名、相对工作区路径（`uploads/<name>`）和字节数，Agent 用文件工具按 SDK cwd 解析；旧 transcript 的 `/uploads/<name>` 兼容解析。
- 渠道入站按 `(channel_id, external_message_id)` 幂等；打包窗口内不保存 `sync_buf`；命令不进 Agent。
- 定时调度在单进程内；不要为 API 启动多个 worker。
- 会话删除/清理：后台清理先清平台产物（截图、投递、渠道绑定）再删 transcript；手动删除先删 transcript 再清理产物。两条路径都幂等，删除期间新提交会被拒绝。
- 平台配置只写 `data/settings.json`；不要恢复 `app_configs` / `providers` / `agents` / `channels` 等旧表。
- 密钥只通过环境变量/`settings.json` 提供，不写入仓库、日志或前端响应。

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
- 只 push `origin`（GitHub）。本地的 `rc-clone` 是独立 checkout，需要同步时在 `rc-clone` 里单独 `git pull`。
- 生产：`qiuqiu@192.168.1.3:~/SuperPersonalPlatform-v1-deploy`，用户级 systemd 服务 `agent-command-center.service`，venv + SQLite，监听 8888；从 GitHub `main` pull，`~/.super-personal-platform/.env` 只在目标机本地。
- 部署与升级步骤见 `README.md`。

## 文档同步

- 行为、接口、配置、依赖、目录结构变化时同步更新 `docs/intent.md`、本文件和 `README.md`。
- 旧架构文档不再作为依据；需要时从 git 历史查看。
