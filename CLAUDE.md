# CLAUDE.md

本文件是新项目 `Agent Command Center` 的协作约定。默认使用中文回复用户。

## 需求与方案

- `docs/intent.md` 是需求唯一来源（F1–F16，2026-11-11 按 SDK 迁移后的实现重写）；没有它覆盖的改动不进入实现。
- 方案先定后做；涉及新依赖、破坏性接口、数据迁移或 schema 变更时，动手前先列出并等确认。
- 每次修改代码后，按用户要求做 3 次自我 code review，并如实标注发现。
- 迁移设计与计划见 `docs/superpowers/specs/2026-11-10-claude-agent-sdk-migration-design.md` 与 `docs/superpowers/plans/`。

## 当前架构

- v1 从 0 到 1 重建；`apps/api` 使用 FastAPI + Claude Agent SDK（Python，`ClaudeSDKClient`），模型走 DeepSeek 的 Anthropic 兼容端点；`apps/web` 使用 React + Vite + TypeScript。
- 执行核：`SessionRegistry`（按 session 串行、跨 session 并发，并发数 = `BROWSER_CONCURRENCY`）+ `ClaudeTurnRunner`（每个 session 一个 `ClaudeSDKClient` 缓存）。
- 会话/消息由 SDK transcript 管理：`~/.super-personal-platform/claude/projects/<agent_id>/<uuid>.jsonl`；会话列表/历史/重命名/删除走 `sdk_bridge` / `session_catalog`，平台不建 sessions/messages/runs 表。
- 平台配置在 `data/settings.json`（`runtime`、`knowledge`、`agents`、`channels`、`channel_conversations`）；单文件、单锁、临时文件 + fsync + `os.replace`，写前留 `.bak`；密钥只返回 `has_api_key` / `has_password`。
- `app.db` 只有六张表：`audit_log`、`cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions`。`market.db` 行情缓存不变。
- `instructions` 通过 SDK `system_prompt={type: preset, preset: claude_code, append: instructions}` 注入；修改 Agent/Provider 后关闭该 Agent 的空闲缓存 client，下一个 turn 生效。
- 重启恢复：`running_sessions` 租约 + `resume`；恢复提示要求先检查状态、继续原任务、不重复已完成步骤；没有 transcript 的租约丢弃；暂停/归档 Agent 的租约保留。
- 网页聊天：`POST /api/chat/messages` → `{session_id, turn_id, accepted}`；SSE `GET /api/sessions/{uuid}/stream?turn_id=...`；历史 `GET /api/sessions/{uuid}/messages?agent_id=...`；中断 `POST /api/sessions/{uuid}/interrupt`；命令 `POST /api/chat/commands`（`/new` `/clear` `/stop` `/status` `/help`）。刷新会丢失当前流式输出，历史从 transcript 重建。
- 渠道：配置与 `channel_conversations`（key `"{channel_id}:{external_id}"`，含 `context_token`）在 `settings.json`；`channel_inbounds` 幂等去重；`channel_deliveries` 在 turn 开始写 pending、结束回填并发送，重启补发；同一联系人 5 秒打包；文本/图片/文件、引用找回与渠道命令都保留；运行期截图实时推送等工具层接入（当前 `channel_images` 无调用方）。
- 定时：`cron_jobs.session_id` 是 SDK 会话 UUID；每次触发在专属会话入队一个 turn；会话忙时跳过并写 `audit_log`；启动补跑最多一次；推送目标是当前 Agent 的 active 微信渠道下已有 1:1 联系人，复用渠道投递。
- 清理：`SessionCleanupRuntime` 按 transcript mtime 与 `runtime.session_retention_days`（默认 30，0 关闭）删除；跳过租约/忙碌会话；先清截图、投递与渠道绑定，再删 transcript；`claude/` 下 auto memory 保留。
- 记忆：用户画像 = `claude/CLAUDE.md`（记忆页单文档编辑，保存留 `.bak`）；Agent 指令在 `settings.json`；Agent 笔记由 Claude auto memory 管理；`agents[].auto_memory` 仅为兼容保留，运行时不再读取。平台没有记忆表、记忆工具、`/memory` 指令或整理记忆。
- 技能：全局 `skills/<name>/SKILL.md`；`DEFAULT_SKILLS` 为 `web-fetch`、`web-search`、`web-browser`、`knowledge-base`、`quant-backtest`、`cron-scheduler`、`trade-journal`、`allocation`、`chanlun`；`.seed_state.json` 记录内置内容哈希，只更新用户未修改的内置技能；已从默认列表移除且未修改的技能在 seed 时删除，用户改过的保留并停止跟踪。
- 工具层未迁移：技能工具、`add_dirs`、MCP、浏览器/搜索/回测/定时/交易/资产/缠论/知识库工具都没有接入 SDK；工具页如实显示 Claude Code 内置工具（Read/Write/Edit/Bash/Glob/Grep）为已接入，平台与技能工具标记为未接入。`langchain-core` 暂留。
- 知识库：`KnowledgeSyncRuntime` 在进程内把 WebDAV 目录双向同步到 `knowledge/`；配置在 `settings.json` 的 `knowledge` 节；本版不挂载给 Agent。
- 业务页面（回测、交易记录、资产配置、缠论图）不绑定 Agent，逻辑与旧版一致：`backtests` 表、`trade-journal.jsonl`、`allocation.json` + `allocation-ledger.jsonl`、`market.db`。
- Agent 工作区固定 `workspaces/<agent_id>/`；归档保留工作区，彻底删除会删除工作区与 transcript 目录。
- 运行时目录 `~/.super-personal-platform/`：`claude/`、`workspaces/<agent>/`、`skills/`、`knowledge/`、`tmp/`、`data/{settings.json, app.db, market.db, knowledge_sync_state.json, trade-journal.jsonl, allocation.json, allocation-ledger.jsonl, browser_state.json, backtests/}`、`.env`。
- 生产：宿主机 systemd 用户服务 + venv + SQLite，单进程 8888；FastAPI 直接托管 `apps/web/dist`。
- UI 沿用珍珠冷光 + 极光 + 玻璃风格；路由为自建 History API（`apps/web/src/routes.ts`），页面 `/chat`、`/config`、`/memory`、`/skills`、`/tools`、`/channels`、`/cron`、`/backtest`、`/journal`、`/allocation`、`/chan`；≤860px 顶部栏 + 抽屉。

## 运行约定

- 同一 session 串行、不同 session 并发；网页、微信、定时统一进入 `SessionRegistry`。
- 网页发送在运行中排队；`/stop` 先取消排队 turn，再打断正在执行的 turn。
- 前端按 session 保存流式状态；刷新丢当前输出，历史从 transcript 读回；不显示消息时间戳。
- 附件保存到 `workspaces/<agent_id>/uploads/`，单文件 10 MB；图片浏览器端缩到最长边 1600 px；消息里写文件名、路径和字节数，Agent 用文件工具读取。
- 渠道入站按 `(channel_id, external_message_id)` 幂等；打包窗口内不保存 `sync_buf`；命令不进 Agent。
- 定时调度在单进程内；不要为 API 启动多个 worker。
- 会话删除/清理先清平台产物（截图、投递、渠道绑定），再删 transcript；auto memory 不清理。
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
