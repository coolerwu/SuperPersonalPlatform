# Agent Command Center

v1：网页对话助手（Chat + 多 Agent + Subagent + Checkpoint/Runs + 配置页），浅色界面，assistant 消息支持 Markdown。

- `apps/api`：FastAPI + deepagents + LangGraph
- `apps/web`：React + Vite + TypeScript
- `deploy/`：systemd unit 与 `.env` 模板（生产）
- 生产：宿主机 systemd + venv + SQLite，单进程监听 8888

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

打开 `http://localhost:5173`，默认 token 见 `APP_AUTH_TOKEN`（默认 `dev-token`）。

本地聊天需要有效模型密钥：通过 `export LLM_API_KEY=...` 或在 `apps/api/.env` 配置；不要提交。也可以登录后在侧栏「配置」里填写。

## Chat

- 默认 Assistant 的 instructions 为中文，参考 Hermes Agent 的默认身份 behavior spec；
- 新对话采用懒创建：点击「新对话」只清空当前视图，首次发送时才创建 Session；
- 对话里以“记住：……”开头的内容会写入用户记忆；
- 用户消息和 assistant 消息都按 Markdown 渲染：GFM 表格/列表、代码高亮、代码块复制、链接和图片；
- 消息操作：复制、引用到输入框；失败 Run 可重试；
- 流式输出、停止生成、自动增高输入框、靠近底部才自动滚动；
- 空状态提供建议提示词；详情抽屉默认关闭，关闭时内容列自动加宽。

## 配置页

侧栏底部「配置」进入配置页，集中管理：

- Provider：查看全部 Provider、密钥是否已配置、新增和编辑；
- Agent：选择并编辑名称、Provider、instructions，或新建 Agent；
- Agent 彻底删除：输入 Agent 名称确认后，级联删除它的会话、消息、Run、事件和 checkpoint（有活动 Run 时拒绝）；
- 运行环境：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关（`GET /api/system`）。

Run 事件存放在 `run_events` 表；前端默认折叠 `assistant_delta` 流式片段，在右侧抽屉里展开查看。

## 记忆

侧栏「记忆」进入独立记忆页面：

- **用户记忆**：所有 Agent 共享，适合偏好、长期事实；
- **Agent 记忆**：只对当前 Agent 生效，适合角色和领域知识；
- 手动新增/编辑/删除/搜索；对话里“记住：……”会立即写入用户记忆；
- Agent 编辑区有「自动学习记忆」开关：开启后，Run 完成时最多返回 12 个记忆操作；先用 FTS5（trigram）找出相关历史记忆，再让模型返回 add/update/delete 做合并，去重后直接写入，每个 Agent 上限 200 条；
- **整理记忆**：把当前 tab 的记忆分批交给模型，只做 update/delete，合并重复、删除失效项，不新增事实；
- 每次 Run 最多注入 12 条、约 1500 字相关记忆，作为数据块附加到系统提示词，不会自动修改 Agent instructions。

## 数据存储

生产数据都在 `~/SuperPersonalPlatform-v1-deploy/data/`：

- `app.db`：SQLite（WAL），文件权限 600；
  - `agents`：Agent 名称、instructions、Provider；
  - `sessions`：会话，首次发送消息时才创建；标题取第一条用户消息前 24 个字；
  - `messages`：每条用户/assistant 消息一行，assistant 消息在 Run 完成时写入；
  - `runs`：每次发送/重试一行，含状态、input/output、错误、token 用量、时间；
  - `run_events`：append-only，含 `run_queued`、`assistant_delta`（每个流式片段一行）、`model_usage`、`run_completed`、`memory_saved` 等；
  - `memory_items`：长期记忆，`scope=user|agent`，含 content、tags、来源 Run/Message、last_used_at；
  - `run_memories`：某次 Run 实际注入了哪些记忆；
  - `providers`：Provider 配置，API Key 存在这里，接口只返回 `has_api_key`，不返回明文；依赖 `app.db` 的文件权限保护。
- `checkpoints/{session_id}.sqlite`：deepagents/LangGraph 的会话状态，用于继续同一 session 的对话；平台消息以 `messages` 为准。

备份时停服务后复制 `data/app.db` 和 `data/checkpoints/`；删除 session 会同时删除它的 messages、runs、run_events 和 checkpoint。

## 配置

| 环境变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `APP_DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite+aiosqlite:///./data/app.db` |
| `APP_DATA_DIR` | 数据/checkpoint 目录 | `./data` |
| `LLM_BASE_URL` | OpenAI-compatible 端点 | `https://api.deepseek.com` |
| `LLM_API_KEY` | 模型密钥 | 空 |
| `LLM_MODEL` | 模型名 | `deepseek-v4-flash` |

## 前端构建产物

`apps/web/dist` 提交到仓库。修改前端后先构建再提交；`package.json` 变化时先 `npm install`：

```bash
cd apps/web && npm install && npm run build
```

生产不再构建 Web 镜像：FastAPI 直接托管已提交的 `apps/web/dist`（含 SPA fallback），API 和页面在同一个进程里。

## V1 生产部署（宿主机 systemd + SQLite）

生产机：`qiuqiu@192.168.1.3`，目录 `~/SuperPersonalPlatform-v1-deploy`，监听 8888。代码从 GitHub `main` 拉取；前端由 FastAPI 直接托管，数据在 `data/app.db` 和 `data/checkpoints`。

首次准备：

```bash
cd ~/SuperPersonalPlatform-v1-deploy
python3.12 -m venv .venv
.venv/bin/pip install -e apps/api
cp deploy/.env.example deploy/.env
# 填 APP_AUTH_TOKEN、LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
mkdir -p data
cp deploy/agent-command-center.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-command-center.service
```

日常更新：

```bash
cd ~/SuperPersonalPlatform-v1-deploy
git pull --ff-only origin main
# 只有 pyproject 依赖变化时才需要重新安装
.venv/bin/pip install -e apps/api
systemctl --user restart agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

`deploy/agent-command-center.service` 使用 `deploy/.env` 作为 `EnvironmentFile`，并强制 `APP_DATABASE_URL=sqlite+aiosqlite:///./data/app.db`、`APP_DATA_DIR=./data`。用户级服务依赖 linger（`loginctl show-user qiuqiu -p Linger` 应为 `yes`），否则退出登录后进程会被回收。

回滚 v1 版本：checkout 上一个已验证的提交，重新安装依赖（如需要）并重启：

```bash
cd ~/SuperPersonalPlatform-v1-deploy && git checkout <commit> && systemctl --user restart agent-command-center.service
```

备份：停服务后复制 `data/app.db` 和 `data/checkpoints/`；或在服务运行时用 SQLite backup API 导出 `app.db`。`data/` 已被 Git 忽略，不会提交。

当前生产为宿主机 systemd + SQLite；Docker Compose、Dockerfile、nginx 与 Postgres 的部署配置已从仓库移除。

## 测试

```bash
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

详细需求见 `docs/intent.md`；协作约定见 `CLAUDE.md`。
