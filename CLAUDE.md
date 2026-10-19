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
- `deploy/`：Docker Compose（Postgres + API + Web）。
- 执行核：`AgentExecutor` 端口 + `DeepAgentExecutor`；平台拥有 session/messages/run，deepagents checkpointer 只存 Agent 状态。
- v1 只做 Chat、多 Agent、默认 general-purpose Subagent、Checkpoint/Runs。
- v1 不暴露 filesystem / skills / memory / shell / browser，也没有审批。

## 运行约定

- 每次用户消息或手动运行创建一个 Run；状态：`queued / running / completed / failed / cancelled`。
- 同一 session 最多一个活动 Run。
- Run 事件 append-only；前端用 SSE 或轮询读取。
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

## 提交约定

- 提交前先跑后端测试与前端构建。
- `git status --short` 确认范围；不要提交 `.super-personal-platform/`、`.env`、`data/`。
- 需要提交时执行 `.claude/skills/project-commit/SKILL.md`；该技能需要随新栈更新。
- 只 push `origin`（GitHub）。本地的 `rc-clone` 是独立 checkout，push 不会改动它；需要同步时在 `rc-clone` 里单独 `git pull`，不要从当前仓库对它执行 pull/reset。
- 旧的部署与版本流程已随旧代码删除；新的生产部署在 `deploy/` 落地后再补文档。

## 文档同步

- 行为、接口、配置、依赖、目录结构变化时同步更新 `docs/intent.md`、本文件和 `README.md`。
- 旧架构文档不再作为依据；需要时从 git 历史查看。
