# GLOSSARY

本项目的领域词汇。文档、规格、ADR、提交信息与代码注释都使用这里的名称，不要另造近义词。

## Agent 与会话

**Agent** — 平台里的一个对话角色：名称、instructions、Provider、状态、权限档位和独立工作区。`default` 是内置助手。
**Agent 工作区** — `workspaces/<agent_id>/`，SDK 进程的 cwd；附件、截图与产物都在这里。受限 Agent 的文件工具只能访问这里。
**会话（session）** — 一个 SDK 会话 UUID；transcript 存在 `claude/projects/<agent_id>/<uuid>.jsonl`。平台不建会话表。
**turn** — 一次用户输入到 Agent 回复结束的执行单元。同一会话的 turn 串行，不同会话并发。
**租约（`running_sessions`）** — turn 开始时写入的运行时记录。`has_turn=1` 表示有 turn 在跑（服务重启时 resume）；`has_turn=0` 只保留后台任务或已结束任务的状态。
**注入轮（injected turn）** — 来源不是人工输入（通知、peer、channel）的 turn，不进入当前轮的展示。
**后台任务（session task）** — turn 结束后仍在运行的 Task 工具任务，由会话级任务区展示，事件走会话级 SSE。
**配置指纹** — 缓存 client 的配置摘要（instructions、Provider、权限档位、全局规则与 Agent 记忆的 mtime/size、插件与技能状态）。指纹变化后下一轮重建 client。

## 输入与渠道

**渠道（channel）** — 一个微信个人号渠道；凭据在 `settings.json` 的 `channels`。
**渠道绑定（`channel_conversations`）** — `"{channel_id}:{external_id}"` → 会话 UUID、显示名与 `context_token`。
**投递（delivery）** — 出站回复义务，存在 `channel_deliveries`：`queued` → `pending` → `sent` / `failed`，重启后补发。
**打包窗口** — 同一联系人的连续消息在静默 6 秒内合并成一个 turn。
**定时任务（cron job）** — `cron_jobs` 表的一行；每次触发在专属会话入队一个 turn，会话忙时跳过。

## 能力与数据

**技能（skill）** — `claude/skills/<name>/SKILL.md` 的 Markdown 说明，所有 Agent 共享，没有启用开关。
**种子技能（seed skill）** — 平台随版本分发的技能模板（`apps/api/app/seed_skills/`），按哈希跟踪初始化状态。
**插件集市** — Claude Code 官方集市 `anthropics/claude-plugins-official`；插件装在 `claude/plugins/`，所有 Agent 共享。
**平台业务工具（`platform_tools`）** — 回测、行情、交易记录、资产配置、缠论共 19 个工具，经进程内 MCP server 以 `mcp__platform_tools__*` 暴露。
**浏览器工具（`browser`）** — 平台进程内的 Playwright MCP server，`mcp__browser__*`；每个会话独立标签页，共享登录态。
**地图工具（`amap`）** — 高德官方 HTTP MCP；启用且有 Key 时以 `mcp__amap__*` 出现。
**产物（artifact）** — Agent 在工作区写出的文件；从工具调用的 `file_path` 或截图类 `filename` 提取，显示在回复下方。
**全局规则** — `claude/CLAUDE.md`，跨 Agent，每次对话开头完整注入。
**Agent 记忆** — `workspaces/<agent_id>/CLAUDE.md`，只对该 Agent 作为项目级 CLAUDE.md 加载。
**auto memory** — Claude Code 自己管理的记忆（`claude/projects/<agent_id>/memory/`），平台只读展示。
**知识库** — WebDAV 目录到 `knowledge/` 的双向镜像。
**随机笔记** — `/knowledge` 页面每次随机展示一篇知识库笔记；洗牌进度存在 `knowledge_deck.json`。

## 权限

**超级 Agent** — `access="full"`，`bypassPermissions`，不挂 hook。
**受限 Agent** — `access="workspace"`，`dontAsk` + 工具白名单 + `PreToolUse` 路径 hook。首版 Bash、Task 与插件工具仍可越界，属于已知限制。
