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
- `packages/contracts`：API 契约/共享类型。
- `deploy/`：systemd unit（生产）；Docker Compose 仅作为可选本地运行方式。
- 执行核：`AgentExecutor` 端口 + `DeepAgentExecutor`；平台拥有 session/messages/run，deepagents checkpointer 只存 Agent 状态。
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`，没有 nginx/Postgres 容器。
- v1 只做 Chat、多 Agent、默认 general-purpose Subagent、Checkpoint/Runs，以及集中配置页。
- v1 不暴露 filesystem / skills / memory / shell / browser，也没有审批。
- 默认 Assistant 的 instructions 为中文，参考 Hermes Agent 的默认身份 behavior spec：直接、长度匹配问题、无 filler、反谄媚、深度按需；默认简体中文回复，并明确 v1 只有 Chat。
- UI 视觉方向：明亮简洁（浅色画布 + 悬浮输入卡片），assistant 消息按 Markdown 渲染；依赖 `react-markdown`、`remark-gfm`、`rehype-highlight`。
- 消息操作：复制、引用、失败 Run 重试；不做编辑/重新生成/删除单条消息（需要 checkpoint 分支方案）。
- Agent 支持彻底删除 `DELETE /api/agents/{id}/permanent`，级联 sessions/messages/runs/run_events/checkpoints；有活动 Run 时 409。
- 配置页集中管理 Provider（查看/新增/编辑）、Agent 和运行环境；`GET /api/system` 只返回非敏感信息。
- Chat 内容列统一宽度：header、消息流、composer 都以 `--chat-lane` 对齐。

## 运行约定

- 每次用户消息或手动运行创建一个 Run；状态：`queued / running / completed / failed / cancelled`。
- Session 懒创建：点击「新对话」只清空视图，首次发送时才创建 Session 和 Run；不要为了展示空状态提前落库。
- 同一 session 最多一个活动 Run。
- Run 事件 append-only，存 `run_events` 表；其中 `assistant_delta` 是每个流式片段一行，SSE/轮询和刷新恢复依赖它。前端默认折叠原始片段，仅保留 Run/工具/子 Agent/用量等可读事件。
- 生产数据库是 SQLite：`~/SuperPersonalPlatform-v1-deploy/data/app.db`；checkpoint 在同目录 `checkpoints/`。时间列用 `UTCDateTime` 保证 SQLite 读回仍是 UTC。
- SQLite 开启 WAL 和 `busy_timeout=5000`，API 与 worker 同进程，不另起服务。
- 每 session 一个 checkpoint thread；刷新/重启后可继续。
- 不把旧 `.super-personal-platform/` 当作数据源；它只是历史用户数据，保持未提交。
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
- 当前 v1 生产：`qiuqiu@192.168.1.3:~/SuperPersonalPlatform-v1-deploy`，用户级 systemd 服务 `agent-command-center.service`，venv + SQLite，监听 8888。旧 Docker Compose / Postgres / nginx 已停用；旧 unit、旧仓库与旧数据已删除，不能回滚到旧平台。
- 生产从 GitHub `main` pull；`deploy/.env` 只存在目标机本地，不提交，作为 systemd `EnvironmentFile`。`apps/web/dist` 提交到仓库，由 FastAPI 直接托管。
- 部署流程见 `README.md` 和 `.claude/skills/project-commit/SKILL.md`。

## 文档同步

- 行为、接口、配置、依赖、目录结构变化时同步更新 `docs/intent.md`、本文件和 `README.md`。
- 旧架构文档不再作为依据；需要时从 git 历史查看。
