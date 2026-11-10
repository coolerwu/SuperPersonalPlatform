# Agent Command Center 迁移到 Claude Agent SDK 设计

- 日期：2026-11-10
- 状态：设计已逐项确认，待评审后进入实现计划
- 范围：仅执行核与聊天数据层；工具层（技能、add_dirs、MCP 工具）不在本版

## 1. 背景与目标

当前平台用 deepagents + LangGraph 自建执行核：LangGraph checkpoint、自研技能中间件、rubric 中间件、模型重试、事件投影，全部由平台维护。实际使用中只有 1 个 Agent、1 个 Provider（DeepSeek）。

迁移动机：

1. **能力**：使用 Claude Code 的成熟 harness（压缩、会话、子 Agent、权限、记忆）。
2. **降维护**：不再维护 checkpoint 恢复、中间件、事件解析。
3. **减少重复建设**：平台只保留调度与业务，把时间投入量化工作。

成功标准：

- 聊天、微信、定时三条入口都跑在 Claude Agent SDK 上。
- 平台代码不再依赖 deepagents / langgraph / langchain-*。
- 重启后正在执行的会话能自动续跑。
- 业务功能（回测、缠论、资产配置、交易记录、知识库）行为不变。

## 2. 总体决策

| 决策 | 内容 |
|---|---|
| 执行核 | Claude Agent SDK（Python，`claude-agent-sdk`），用 `ClaudeSDKClient` |
| 模型 | DeepSeek 官方 Anthropic 兼容端点 `https://api.deepseek.com/anthropic` |
| 会话/消息 | 使用 SDK 原生 transcript；平台不建 sessions / messages / runs / run_events 表 |
| 流式 | 只做实时转发；刷新丢当前输出（已接受） |
| 重启恢复 | 单列表 `running_sessions` + SDK `resume` |
| 配置 | 单文件 `data/settings.json` |
| 数据迁移 | 不做；旧库与 checkpoint 直接丢弃 |

被接受的能力损失：

- 刷新或断线后无法续看当前输出；
- 聊天气泡无时间戳；
- 无会话全文搜索；
- 失败轮次无过程重建，用户重发即可；
- 排队中的追问在重启后丢失。

## 3. 目录与配置

### 3.1 运行时目录

```
~/.super-personal-platform/
├── claude/                        # CLAUDE_CONFIG_DIR
│   ├── CLAUDE.md                  # 用户画像（跨 Agent）
│   ├── settings.json              # Claude Code 自身配置（autoMemoryDirectory 等）
│   └── projects/<project>/
│       ├── <uuid>.jsonl           # SDK 会话 transcript
│       └── memory/                # auto memory（按项目/Agent）
├── workspaces/<agent_id>/         # ClaudeSDKClient 的 cwd
│   ├── CLAUDE.md                  # 可选：Agent 专属指令
│   ├── uploads/
│   └── screenshots/
├── skills/                        # 技能库（本版不动，工具层处理）
├── knowledge/                     # 知识库镜像
├── tmp/                           # TMPDIR
├── data/
│   ├── app.db
│   ├── market.db
│   ├── settings.json              # 平台配置
│   ├── knowledge_sync_state.json
│   ├── trade-journal.jsonl
│   ├── allocation.json
│   ├── allocation-ledger.jsonl
│   ├── browser_state.json
│   └── backtests/
├── venv/  .env
```

约定：`data/settings.json` 是平台配置；`claude/settings.json` 是 Claude Code 配置，两者无关。

### 3.2 `data/settings.json`

```json
{
  "runtime": { "session_retention_days": 30 },
  "knowledge": {
    "enabled": false, "url": "", "username": "", "password": "",
    "root": "", "interval_seconds": 300
  },
  "agents": [
    {
      "id": "default",
      "name": "个人助手",
      "instructions": "...",
      "status": "active",
      "enabled_skills": [],
      "provider": {
        "name": "deepseek",
        "base_url": "https://api.deepseek.com/anthropic",
        "model": "deepseek-flash[1m]",
        "api_key": ""
      },
      "created_at": "2026-11-10T00:00:00Z"
    }
  ],
  "channels": [],
  "channel_conversations": {}
}
```

读写规则：

- 单文件、单 `asyncio.Lock`；写临时文件 → fsync → `os.replace`；写前留 `.bak`。
- 读失败时先尝试 `.bak`，再失败按节回退默认值并记日志，服务照常启动。
- 环境变量只做首次种子（`SESSION_RETENTION_DAYS`、`KB_WEBDAV_*`、默认 Agent 提示词）；文件存在后不再读取。
- `provider.api_key`、`knowledge.password` 仅执行器内部读取；接口只返回 `has_api_key` / `has_password`，日志与 `GET /api/system` 不出现。
- 当前 Agent 选择仍由前端 `localStorage` 保存。

### 3.3 进程环境

每个 `ClaudeSDKClient` 通过 `env` 注入：

```
CLAUDE_CONFIG_DIR=~/.super-personal-platform/claude
CLAUDE_CODE_PROJECT_DIR_NAME=<agent_id>
TMPDIR=~/.super-personal-platform/tmp
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN=<provider.api_key>
ANTHROPIC_MODEL=deepseek-flash[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-flash[1m]
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-flash
CLAUDE_CODE_SUBAGENT_MODEL=deepseek-flash
CLAUDE_CODE_EFFORT_LEVEL=max
CLAUDE_CODE_AUTO_COMPACT_WINDOW=786432
```

- `HOME` 保持真实值，不注入平台目录，避免 shell 工具（npm/uv 等）缓存与配置失效；SDK 自身的落盘范围由 `CLAUDE_CONFIG_DIR` 与 `TMPDIR` 控制。
- `setting_sources=["user", "project"]`，以加载用户级与项目级 CLAUDE.md。
- 关闭模型类重试，重试由 SDK/CLI 与平台 Run 层承担。

## 4. 执行核

### 4.1 结构

- `SessionRegistry`：内存维护 `session_uuid → ClaudeSDKClient`，懒连接。
- 每个平台会话一个 `asyncio.Queue`，同一会话串行，不同会话并发，上限沿用现有并发配置（默认 5）。
- 每轮生成独立 `turn_id` 与 `TurnStream`，SSE 按 `turn_id` 订阅；已完成的流保留在有限容量缓存（超出淘汰），未命中时前端回退到 transcript。
- 平台会话 ID 由平台生成 UUID，直接作为 SDK 的 `session_id`；若磁盘已有 transcript 则用 `resume=<uuid>`。

### 4.2 一轮执行

```
1. 入队（网页 / 微信 / 定时统一入口）
2. INSERT running_sessions(session_id)；渠道或定时推送来源同时 INSERT channel_deliveries(status='pending')，两条在同一事务提交
3. 提交后再把 prompt 交给 SDK
4. client = registry.get_or_create(uuid)
5. await client.query(prompt)
6. async for msg in client.receive_response():
     - StreamEvent text_delta / thinking_delta → SSE
     - AssistantMessage 工具调用 → SSE
     - ResultMessage → 本轮结束
7. 终态处理：
     success        → completed
     error_*        → failed
     aborted_*      → cancelled
8. 渠道来源：回填 delivery 内容并发送
9. finally: DELETE running_sessions、更新 client 空闲计时
```

### 4.3 取消

`POST /api/sessions/{uuid}/interrupt` → `client.interrupt()` → 继续 drain 到 ResultMessage → 删租约 → 前端显示已停止。不再使用数据库 `cancel_requested`。

### 4.4 重启恢复

```
for row in running_sessions:
    if not exists(<config>/projects/<agent>/<uuid>.jsonl):
        DELETE row; continue
    client = create(resume=uuid, cwd=workspaces/<agent>)
    client.query("上次任务因服务重启被中断。请先检查当前状态，继续完成原任务；已完成的步骤不要重复执行。")
    执行完成后 DELETE row
```

- 恢复失败（transcript 损坏、端点不可用）：删行、记日志，不重试，避免每次启动卡死。
- 恢复的轮次没有前端订阅；用户打开会话看到最终结果。
- 单 Agent 假设：cwd 固定 `workspaces/default`；多 Agent 时需在表中增加 `agent_id`。

## 5. 会话与前端接口

### 5.1 会话

- 进程启动时把 `CLAUDE_CONFIG_DIR` 写入**进程环境**：SDK 的 in-process 会话 API 读进程 env，CLI 子进程读 `options.env`，两者必须指向同一目录。
- 会话列表用 `list_sessions()`（不带 directory，扫描全部项目）后按 `session.cwd == workspaces/<agent>` 过滤；`CLAUDE_CODE_PROJECT_DIR_NAME=<agent_id>` 只决定磁盘目录名，不参与 SDK 枚举。
- `rename_session` / `get_session_messages` 不传 directory（按会话 UUID 在全部项目目录中查找）。
- 新建会话只在前端生成 UUID，首次发送才产生 transcript。
- 会话 retention：按 `last_modified` 删除过期 transcript，跳过 `running_sessions` 中的会话，不碰 `memory/` 目录。
- 微信绑定（`channel_conversations`）指向已删除会话时，下次入站自动生成新 UUID 重新绑定。

### 5.2 接口变化

| 现在 | 迁移后 |
|---|---|
| `POST /api/chat/messages` | 入队并返回 `{session_id, turn_id, accepted}` |
| `GET /api/runs/{id}/events`（SSE） | `GET /api/sessions/{uuid}/stream?turn_id=...`，转发该轮的 SDK 流 |
| `GET /api/sessions/{id}/messages/page` | `GET /api/sessions/{uuid}/messages`，一次返回 transcript |
| `GET /api/sessions/{id}/active-run` | 删除，查内存 registry |
| `POST /api/runs/{id}/cancel` | `POST /api/sessions/{uuid}/interrupt` |

### 5.3 前端

删除：`run_events` 折叠执行过程、`assistant_replace`、事件去重、轮询退避、消息分页、全文搜索命中定位。

保留：Markdown/公式渲染、附件、引用、命令面板、草稿、移动端、会话列表。

执行过程：实时可见；刷新后从 transcript 的 thinking / tool_use 块尽量重建，信息少于现在。

消息时间：不显示。

## 6. 渠道

### 6.1 入站

1. iLink 长轮询 → 按联系人 5 秒打包；
2. `channel_inbounds`（渠道 + 外部消息 id）去重；
3. 在 `settings.json` 的 `channel_conversations[external_id]` 找会话 UUID，没有则生成；更新 `context_token`；
4. 入队一轮。

打包窗口内不写 `sync_buf`；崩溃后由 iLink 重放、幂等表吸收。

### 6.2 投递

`channel_deliveries` 新结构：

```sql
channel_deliveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  delivered_at TEXT
)
```

- 在**轮次开始**时插入 pending，结束后回填内容并发送；崩溃重启后按 `session_id` 找到 pending 行补发。
- 只有入站/定时推送触发的轮次创建投递记录；网页消息与网页重试不推送。
- `channel_run_links` 表删除。

### 6.3 命令与截图

- `/new`：flush → 新 UUID → 重绑；`/stop`：interrupt；`/status`：Agent、会话 UUID、队列长度、租约状态。
- 运行期 `browser_screenshot` 继续通过当前 `context_token` 实时上传。

## 7. 定时

- 保留 `cron_jobs`，`session_id` 改为 UUID 字符串，删除 `last_run_id`。
- 每次触发：找/生成会话 UUID（transcript 不存在则重新生成）→ 会话忙则写 `audit_log` 跳过 → 入队。
- 配了推送目标时，轮次开始写入 pending 投递，与渠道共用发送与补发逻辑。
- 启动时过期任务最多补跑一次；单进程调度不变。

## 8. 记忆

| 层 | 位置 | 写入方 |
|---|---|---|
| 用户画像 | `claude/CLAUDE.md` | 用户（网页编辑或直接改文件） |
| Agent 指令 | `settings.json` `agents[].instructions` | 用户 |
| Agent 笔记 | `claude/projects/<project>/memory/` | Claude（auto memory + Auto Dream） |

- 平台不注入记忆；系统提示词即 `instructions`。
- 记忆页只展示/编辑 `CLAUDE.md`；auto memory 只做只读展示（可选）。
- `/memory` 指令、整理记忆技能、记忆工具与相关表全部删除。
- Agent 对 `claude/` 目录的可写性由工具层 deny 规则处理（本版未开）。

## 9. 数据模型

最终 `app.db` 六张表：

| 表 | 功能 |
|---|---|
| `audit_log` | 审计流水（重启、定时跳过、渠道异常） |
| `cron_jobs` | 定时任务定义与 `next_run_at` |
| `channel_inbounds` | 入站幂等 |
| `channel_deliveries` | 出站投递义务与重试 |
| `backtests` | 回测任务与结果 |
| `running_sessions` | 运行时租约（`session_id TEXT PRIMARY KEY`） |

`market.db` 两张行情表不变。

迁出数据库的：`providers`、`agents`、`channels`、`channel_conversations`、`app_configs`、`knowledge_configs` → `settings.json`；`sessions`、`messages`、`runs`、`run_events` → SDK；`memory_items`、`run_memories`、`channel_run_links` → 删除。

## 10. 删除清单

- 依赖：`deepagents`、`langgraph`、`langgraph-checkpoint-sqlite`、`langchain-openai`、`langchain-core`（工具层迁移完成后）、`langchain-quickjs`（eval 不在本版）。
- 代码：`executor.py` 旧实现、`skill_middleware.py`、`rubric.py`、`model_retry.py`、`session_tools.py`、`memory_tools.py`、`session_search.py`、`run_service.py` 的 checkpoint 相关逻辑。
- 数据：`data/checkpoints/`、旧 `app.db`（重建）。
- 前端：Run 事件与分页相关组件。

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| DeepSeek 端点非 Anthropic 官方支持，字段会被忽略或行为不同 | 固定 SDK 版本；上线前用真实 key 跑集成冒烟（流式、工具、压缩、resume） |
| SDK 0.x 快速迭代导致接口变动 | 执行核包一层 `ClaudeAgentExecutor`，SDK 调用集中在单文件 |
| `bypassPermissions` 下 Agent 可写平台配置 | 工具层加 deny 规则或调整权限模式；本版先记录风险 |
| 自动压缩与 auto memory 在 DeepSeek 端点下的实际表现 | 迁移验证项中显式测试 |
| 单进程 cron 与内存队列，重启丢排队消息 | 已接受；文档与 UI 提示 |
| 会话清理误删运行中会话 | 清理跳过 `running_sessions` |

## 12. 本版不包含

- 工具层：`add_dirs`、技能列表、技能工具到进程内 MCP 工具的迁移；
- rubric 质检；
- QuickJS eval 代码解释器；
- 会话全文搜索；
- 多 Agent 支持。

## 13. 验收标准

1. 新建会话 → 发消息 → 流式回复 → 会话出现在列表；刷新后历史可读。
2. 执行中取消 → 状态为已停止，租约删除。
3. 执行中重启服务 → 该会话自动 resume 并完成；`running_sessions` 清空。
4. 微信入站 → 5 秒打包 → 回复发回同一联系人；断网重放不重复处理。
5. 微信会话 `/new`、`/stop`、`/status` 正常。
6. 定时任务按时触发，会话忙时跳过并写审计；配置推送目标时送达微信。
7. 会话超过保留天数被清理，运行中的不被清理，auto memory 保留。
8. 回测、缠论、资产配置、交易记录、知识库页面功能无回归。
9. `grep -r "deepagents\|langgraph\|langchain_openai" apps/api` 无结果。
10. `claude/CLAUDE.md` 的用户画像在新会话中生效；auto memory 跨会话生效，且 Auto Dream 在 DeepSeek 端点下可运行（若不可运行，记录并降级为不依赖它）。
