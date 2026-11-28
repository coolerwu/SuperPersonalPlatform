# Agent Command Center

v1：单用户自托管的网页对话助手。执行核是 Claude Agent SDK（Python，`ClaudeSDKClient`），模型走 DeepSeek 的 Anthropic 兼容端点；网页、微信、定时三条入口共用同一执行核。

- `apps/api`：FastAPI + Claude Agent SDK（Python 3.12）
- `apps/web`：React + Vite + TypeScript；构建产物 `apps/web/dist` 提交到仓库
- `deploy/`：systemd unit 与 `.env` 模板（生产）
- 生产：宿主机 systemd 用户服务 + venv + SQLite，单进程监听 8888；FastAPI 直接托管 `apps/web/dist`

会话与消息由 SDK transcript 管理（`claude/projects/<agent_id>/<uuid>.jsonl`）；平台配置在 `data/settings.json`；新库的 `app.db` 只有 `cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 五张表（从旧版升级的库会保留未使用的旧表）。

## 运行时目录

配置与数据统一在 `~/.super-personal-platform/`（`APP_HOME` 可覆盖）：

```text
~/.super-personal-platform/
├── .env                    # APP_AUTH_TOKEN、LLM_* 等配置
├── claude/                 # CLAUDE_CONFIG_DIR（权限 0700）
│   ├── CLAUDE.md           # 全局规则，跨 Agent 生效（记忆页编辑）
│   ├── settings.json       # Claude Code 自身配置（插件启用状态、cleanupPeriodDays）
│   ├── skills/             # 全局技能库（.seed_state.json 记录初始化技能哈希）
│   ├── plugins/            # 官方集市与已安装插件（插件页管理）
│   └── projects/<agent_id>/
│       ├── <uuid>.jsonl    # SDK 会话 transcript
│       └── memory/         # Claude auto memory
├── workspaces/<agent_id>/  # SDK 的 cwd；uploads/、screenshots/、CLAUDE.md（Agent 记忆）
├── knowledge/              # 坚果云知识库的本地镜像
├── tmp/                    # SDK 的 TMPDIR
└── data/
    ├── settings.json       # 平台配置（0600，写前留 .bak）
    ├── app.db              # SQLite（新库五张表；升级库保留旧表）
    ├── market.db           # Bitget 行情缓存
    ├── knowledge_sync_state.json
    ├── knowledge_deck.json # 随机笔记的洗牌进度
    ├── trade-journal.jsonl
    ├── allocation.json
    ├── allocation-ledger.jsonl
    ├── browser_state.json  # 浏览器登录态（平台浏览器工具跨会话复用）
    └── backtests/          # 回测策略临时文件
```

`data/settings.json` 是平台配置，`claude/settings.json` 是 Claude Code 自身配置，两者无关。

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

打开 `http://localhost:5173`，默认 token 见 `APP_AUTH_TOKEN`（默认 `dev-token`）。模型密钥可通过 `export LLM_API_KEY=...`、`~/.super-personal-platform/.env` 或启动目录 `.env` 提供，也可以登录后在「配置 → Provider」填写；不要提交。语音输入需要本机 `ffmpeg`（macOS `brew install ffmpeg`，Ubuntu `apt install ffmpeg`）。

## 配置

平台配置由配置页写入 `data/settings.json`（`runtime`、`knowledge`、`maps`、`asr`、`agents`、`channels`、`channel_conversations`）；接口只返回 `has_api_key` / `has_password` / `has_credentials`。`.env` 只在首次种子时复制进 `settings.json`，之后以文件为准。

常用环境变量（完整列表与默认值见 `apps/api/app/config.py`）：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_HOME` | 配置与数据根目录 | `~/.super-personal-platform` |
| `APP_AUTH_TOKEN` | 单用户访问 token | `dev-token` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 首次种子：Provider 端点（自动补 `/anthropic`）、密钥、模型 | DeepSeek 端点 / 空 / `deepseek-v4-flash` |
| `SESSION_RETENTION_DAYS` | 首次种子：会话保留天数（0 写成 36500） | `30` |
| `RUN_TIMEOUT_SECONDS` | 单个 turn 总超时 | `900` |
| `BROWSER_CONCURRENCY` | 跨 session 并发上限，同时是浏览器标签页上限 | `5` |
| `BROWSER_IDLE_SECONDS` | 浏览器空闲回收秒数 | `300` |
| `CLAUDE_CLIENT_TTL_SECONDS` / `CLAUDE_CLIENT_MAX_CLIENTS` | 缓存 SDK client 的空闲 TTL / 上限 | `300` / `8` |
| `CRON_TICK_SECONDS` / `CRON_DEFAULT_TIMEZONE` | 定时扫描间隔 / 新建任务默认时区 | `20` / `Asia/Shanghai` |
| `CHANNEL_INBOUND_BATCH_SECONDS` | 同一联系人连续消息的打包窗口（0 关闭） | `6` |
| `CHANNEL_REPLY_MAX_CHARS` / `CHANNEL_DELIVERY_MAX_ATTEMPTS` | 微信单块字符上限 / 发送尝试上限 | `1800` / `3` |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | 统一出站代理（LLM、微信、行情共用） | 空 |
| `BACKTEST_TIMEOUT_SECONDS` / `BACKTEST_MEMORY_LIMIT_MB` | 策略子进程超时 / 内存上限 | `120` / `2048` |

## 现行能力概览

- **Chat**：流式输出、历史会话、停止、运行中排队追问（可单条撤回）、Markdown/公式渲染、附件、整条与划词引用、语音输入；刷新时同进程重放当前轮缓冲，SSE 断线按事件序号续传。
- **多 Agent**：每个 Agent 独立工作区、会话目录、instructions、Provider 与权限档位；`active` / `paused` / `archived` 三种状态。
- **执行核**：`ClaudeSDKClient`；同一会话串行、不同会话并发；重启后按 `running_sessions` 租约自动 resume。
- **后台任务**：turn 结束后仍在运行的 Task 由会话级任务区展示，刷新/重连可恢复。
- **微信渠道**：扫码登录、iLink 长轮询、6 秒打包、命令、引用找回、图片/文件、回复投递与重启补发。
- **定时任务**：5 字段 cron、IANA 时区、专属会话、忙时跳过、可选推送到微信联系人。
- **技能与插件**：全局技能库（带 seed 哈希跟踪）与官方插件集市；工具以运行时 init frame 为准。
- **工具**：平台业务工具（回测、行情、交易记录、资产配置、缠论，共 19 个）与浏览器工具（13 个）经进程内 MCP 提供；地图为高德官方 HTTP MCP（可选）。
- **记忆**：全局规则（`claude/CLAUDE.md`）与 Agent 记忆（`workspaces/<agent_id>/CLAUDE.md`）；Claude auto memory 只读展示。
- **知识库与随机笔记**：WebDAV 双向镜像；`/knowledge` 页面随机展示一篇笔记，可带原文追问。
- **业务页面**：回测、交易记录、资产配置、缠论图，均不绑定 Agent。
- **不包含**：平台自带搜索工具、`add_dirs`、知识库工具接入 Agent、rubric 质检、QuickJS eval、会话全文搜索、Run 历史、消息时间戳、进程重启后继续看当前输出。

## 前端构建产物

`apps/web/dist` 提交到仓库。修改前端后先构建再提交；`package.json` 变化时先 `npm install`：

```bash
cd apps/web && npm install && npm run build
```

生产不构建 Web 镜像：FastAPI 直接托管已提交的 `apps/web/dist`（含 SPA fallback），API 和页面在同一个进程里。

## V1 生产部署（宿主机 systemd + SQLite）

生产机：`qiuqiu@192.168.1.3`，代码目录 `~/SuperPersonalPlatform-v1-deploy`，监听 8888。代码从 GitHub `main` 拉取；配置和数据在 `~/.super-personal-platform/`。systemd unit 使用 `KillMode=mixed`：重启时 SIGTERM 只发给 uvicorn，SDK CLI 子进程保持到应用取消 turn、保留恢复租约后再退出；uvicorn 带 `--timeout-graceful-shutdown 10`。关机顺序为 registry → runner → browser → 其他服务，`TimeoutStopSec=90` 覆盖整体预算。不要改回默认 `KillMode` 或去掉这两个参数，否则重启后无法续跑。

### 首次准备

```bash
cd ~/SuperPersonalPlatform-v1-deploy
python3.12 -m venv .venv
.venv/bin/pip install -e apps/api
.venv/bin/playwright install chromium
mkdir -p ~/.super-personal-platform/data
cp deploy/.env.example ~/.super-personal-platform/.env
# 编辑 ~/.super-personal-platform/.env，填 APP_AUTH_TOKEN、LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
# 需要代理时设置 HTTP_PROXY / HTTPS_PROXY（生产机可用 http://192.168.1.2:7893）
# 语音输入需要 ffmpeg/ffprobe；知识库优先在配置页「知识库」分区配置
cp deploy/agent-command-center.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

登录后在「配置 → Provider」确认 DeepSeek Anthropic 端点（`https://api.deepseek.com/anthropic`）、model 与 API key。

### 日常更新

```bash
cd ~/SuperPersonalPlatform-v1-deploy
git pull --ff-only origin main
# 只有 pyproject 依赖变化时才需要重新安装
.venv/bin/pip install -e apps/api
systemctl --user restart agent-command-center.service
curl --noproxy '*' http://127.0.0.1:8888/api/health
```

`deploy/agent-command-center.service` 使用 `~/.super-personal-platform/.env` 作为 `EnvironmentFile`，并强制 `APP_DATA_DIR` / `APP_DATABASE_URL` 指向 `~/.super-personal-platform/data/`。用户级服务依赖 linger（`loginctl show-user qiuqiu -p Linger` 应为 `yes`）。

### 回滚与重建

```bash
cd ~/SuperPersonalPlatform-v1-deploy && git checkout <commit> && systemctl --user restart agent-command-center.service
```

不要回退 SQLite 数据文件：会话数据在 SDK transcript 里，回滚代码会看不到新会话。彻底重建 `data/app.db` 会丢失回测历史、定时任务、未完成投递与入站去重记录；`settings.json`（含渠道绑定）和 transcript 不受影响。升级前备份 `data/app.db`、`data/settings.json`、`claude/`、`workspaces/` 与 `data/trade-journal.jsonl`、`data/allocation*.json*`。

## 测试

```bash
.venv/bin/python -m pytest apps/api/app/tests
cd apps/web && npm run build
```

## 文档

- `CLAUDE.md` — 当前架构、运行约定与协作规则（agent 契约）
- `GLOSSARY.md` — 领域词汇
- `docs/adr/` — 仍约束未来改动的决定
- `docs/agents/` — agent skills 的仓库配置（issue tracker、triage 标签、domain docs）
- `.scratch/<feature>/spec.md` — 新需求的规格（`/to-spec`）
