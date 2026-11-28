# ADR-0001: 会话与消息由 SDK transcript 管理

- 状态：Accepted
- 日期：2026-11-29（决定来自 2026-11-11 的 SDK 迁移）

## 背景

v0 用 deepagents + LangGraph 自建执行核，平台维护 sessions / messages / runs / run_events 与 checkpoint。会话状态、执行过程与恢复都在平台侧，维护成本高，且与 Claude Code 自身能力（压缩、子 Agent、resume）重复。

## 决策

- 会话的唯一存储是 SDK transcript：`claude/projects/<agent_id>/<uuid>.jsonl`；平台生成的 UUID 直接作为 SDK `session_id`。
- 会话列表、历史、重命名、删除都走 SDK（`sdk_bridge` / `session_catalog`）；平台不建 sessions / messages / runs 表。
- 重启恢复用 `running_sessions` 租约 + SDK `resume`；排队中的 turn 不恢复。
- 保留清理交给 Claude Code：平台把 `runtime.session_retention_days` 同步到运行时 `claude/settings.json` 的 `cleanupPeriodDays`，不跑清理任务。

## 后果

- 新库的 `app.db` 只剩 `cron_jobs`、`channel_inbounds`、`channel_deliveries`、`backtests`、`running_sessions` 五张表。
- 上下文压缩后，压缩前的消息不再出现在历史里（`get_session_messages` 只返回压缩点之后的链）。
- 进程重启后当前流式输出丢失；同进程刷新通过 `current_turn_id` 重放缓冲，同一 turn 内 SSE 断线按事件序号续传。
- 删除会话前必须原子标记并拒绝新提交，再尽力清理平台产物（截图、投递、渠道绑定）。

## 被否方案

- 平台表与 transcript 双写：两个事实来源必然漂移，恢复逻辑又需要自己维护。
