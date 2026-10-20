# Agent Command Center

v1：网页对话助手（Chat + 多 Agent + Subagent + Checkpoint/Runs + 配置页），浅色界面，assistant 消息支持 Markdown。

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
└── skills/              # 全局技能库（每个技能一个目录）
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
- 流式输出、停止生成、自动增高输入框、靠近底部才自动滚动；
- Agent 可在当前工作区内查看、创建和修改文件（ls / read_file / write_file / edit_file / delete / glob / grep）；没有 Shell 工具；浏览器能力通过 `web-browser` 技能解锁（无头 Chromium，只返回文字和元素引用，不返回截图）；
- 技能与工具：左侧「技能」页面为当前 Agent 启用技能；模型读取 `/skills/<name>/SKILL.md` 后，技能声明的工具才会解锁；文件工具始终可用；「工具」页面只展示注册与声明情况；
- 空状态提供建议提示词；详情抽屉默认关闭，关闭时内容列自动加宽。
- 顶部轻量栏显示当前 Agent 和会话标题，并作为详情抽屉开关。
- 响应式：桌面为侧栏 + 对话 + 详情三栏；窄屏（≤860px）用顶部栏和汉堡菜单打开侧栏抽屉，详情变为全屏面板，输入区跟随软键盘与底部安全区。

## 配置页

侧栏底部「配置」进入配置页，顶部用 `Provider / Agent / 运行环境` 三个 tab 切换，不需要一路滚到底；Provider/Agent 默认只显示列表，点「编辑」或条目再展开表单。**配置页始终编辑左上角侧边栏选中的 Agent，不提供第二套 Agent 选择**；切换 Agent 请使用侧边栏顶部的选择器。切换 Agent 时，Provider tab 会自动选中该 Agent 的 Provider，并清空输入框草稿。集中管理：

- Provider：查看全部 Provider、密钥是否已配置、新增和编辑；
- Agent：选择并编辑名称、Provider、instructions、工作区，或新建 Agent；新建时未填写 instructions 会使用默认的中文个人助手提示词（能力说明 + 行为规范）；
- 工作区：每个 Agent 固定使用 `~/.super-personal-platform/workspaces/<agent_id>/`，不支持自定义路径；配置页只读展示机器目录，并随左上角选中的 Agent 变化。Agent 可以查看和修改工作区内的文件；
- 技能：在侧边栏左上角的「技能」页面按当前 Agent 勾选启用；未启用的技能不会进入系统提示，读取它的 SKILL.md 也不会解锁工具。磁盘上已删除但仍在启用列表里的技能会显示为“未找到”，可取消勾选移除；
- Agent 彻底删除：输入 Agent 名称确认后，级联删除它的会话、消息、Run、事件和 checkpoint（有活动 Run 时拒绝）；
- 运行环境：版本、执行核、数据库类型、Run 超时、历史消息上限、计数和能力开关（`GET /api/system`）。

Run 事件存放在 `run_events` 表；前端默认折叠 `assistant_delta` 流式片段，在右侧抽屉里展开查看。

## 技能与工具页面

侧边栏左上角（Agent 选择器下方）提供两个独立页面：

- **技能**：列出全局技能库，按当前 Agent 勾选/取消启用，立即保存、下一轮 Run 生效；展示 description、声明的工具、SKILL.md 路径和解析警告。
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
- 首次启动会在技能库中 seed `web-fetch`、`web-search` 和 `web-browser` 三个技能（已存在则不覆盖），分别声明 `include_tools: web_fetch` / `web_search` / 六个 `browser_*` 工具。

## 工具

平台注册表当前提供以下联网工具：

- **`web_search`**：调用 DeepSeek Search API（`POST {provider.base_url}/v1/tools/search`），请求 `query`（1-128 字）、`count`（1-50，默认 10）、`need_content`，返回标题、链接、摘要、来源、日期、权威性评分。支持 `site:`、`-`、引号、`lang:`、`after:`、`before:` 等搜索语法。
- **`web_fetch`**：调用 DeepSeek Fetch API（`POST {provider.base_url}/v1/tools/fetch`），请求 `{url}`，返回 `{title, content}`（Markdown）。`url` 必须是完整 http/https 地址，不能带用户名/密码。
- **浏览器工具**（Playwright，本地无头 Chromium）：
  - `browser_open(url, tab="main")`：打开网页，返回标题、正文和可交互元素 ref；
  - `browser_snapshot(tab="main")`：读取当前标签页的最新快照；
  - `browser_click(ref, tab)`：点击快照中的元素；
  - `browser_fill(ref, text, tab, submit=false)`：填写输入框或 select；
  - `browser_press(key, tab)`：按下按键；
  - `browser_close(tab)`：关闭标签页并释放并发槽位。
  - 每次 Run 懒启动一个浏览器进程和一个 context，最多 `BROWSER_CONCURRENCY=2` 个标签页；Run 结束、失败、超时或取消时自动关闭。ref 只对最近一次快照有效，动作后会返回新快照。
  - 只允许公开 http/https 地址，拒绝 localhost、内网/回环/链路本地地址、带凭据 URL、`file://` 和下载；只处理主 frame，不接管弹窗。

`web_search` 和 `web_fetch` 使用当前 Agent Provider 的 key，需要 Provider 支持 DeepSeek Search/Fetch；成功后按请求计费（各每 1000 次 5 元），分别受 DeepSeek QPS 限制（Search 100、Fetch 25）。使用条件：在「技能」页面启用声明它的技能（`web-search` / `web-fetch` / `web-browser`），对话中读取该 SKILL.md 后解锁。

工具实现分别在 `apps/api/app/services/skill_tools.py`（Search/Fetch）和 `apps/api/app/services/browser_tools.py`（浏览器）；新增工具时注册一个 factory，按运行时 Provider/会话构建 LangChain `BaseTool`。

## 记忆

侧栏「记忆」进入独立记忆页面：

- **用户记忆**：所有 Agent 共享，适合偏好、长期事实；
- **Agent 记忆**：只对当前 Agent 生效，适合角色和领域知识；
- 手动新增/编辑/删除/搜索；对话里“记住：……”会立即写入用户记忆；
- Agent 编辑区有「自动学习记忆」开关：开启后，Run 完成时最多返回 12 个记忆操作；先用 FTS5（trigram）找出相关历史记忆，再让模型返回 add/update/delete 做合并，去重后直接写入，每个 Agent 上限 200 条；
- **整理记忆**：把当前 tab 的记忆分批交给模型，只做 update/delete，合并重复、删除失效项，不新增事实；
- 每次 Run 最多注入 12 条、约 1500 字相关记忆，作为数据块附加到系统提示词，不会自动修改 Agent instructions。

## 数据存储

生产数据都在 `~/.super-personal-platform/data/`：

- `app.db`：SQLite（WAL），文件权限 600；
  - `agents`：Agent 名称、instructions、Provider、`enabled_skills`（启用的技能名 JSON 列表）；
  - `sessions`：会话，首次发送消息时才创建；标题取第一条用户消息前 24 个字；
  - `messages`：每条用户/assistant 消息一行，assistant 消息在 Run 完成时写入；
  - `runs`：每次发送/重试一行，含状态、input/output、错误、token 用量、时间；
  - `run_events`：append-only，含 `run_queued`、`assistant_delta`（每个流式片段一行）、`model_usage`、`run_completed`、`memory_saved` 等；
  - `memory_items`：长期记忆，`scope=user|agent`，含 content、tags、来源 Run/Message、last_used_at；
  - `run_memories`：某次 Run 实际注入了哪些记忆；
  - `providers`：Provider 配置，API Key 存在这里，接口只返回 `has_api_key`，不返回明文；依赖 `app.db` 的文件权限保护。
- `checkpoints/{session_id}.sqlite`：deepagents/LangGraph 的会话状态，用于继续同一 session 的对话；平台消息以 `messages` 为准。
- `~/.super-personal-platform/workspaces/`：每个 Agent 的固定工作区目录，名称为 Agent id；删除 Agent 不会删除这里的文件。
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
| `BROWSER_CONCURRENCY` | 每个 Run 的浏览器标签页上限 | `2` |
| `BROWSER_NAVIGATION_TIMEOUT_MS` | 页面导航超时 | `30000` |
| `BROWSER_ACTION_TIMEOUT_MS` | 点击/填写动作超时 | `10000` |
| `BROWSER_MAX_ELEMENTS` | 每次快照的交互元素上限 | `120` |
| `BROWSER_MAX_TEXT_CHARS` | 每次快照回传的正文上限 | `4000` |
| `BROWSER_MAX_SNAPSHOT_CHARS` | 单次快照总字符上限 | `12000` |
| `BROWSER_NO_SANDBOX` | Chromium 启动失败时的逃生开关 | `false` |
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
