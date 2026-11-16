# 会话级后台任务事件通道设计

- 日期：2026-11-16
- 状态：待实施
- 关联问题：审计 W3（跨轮后台任务事件延迟/丢失）
- 关联实现：`apps/api/app/models.py`、`apps/api/app/services/turn_lease.py`、`apps/api/app/services/recovery.py`、`apps/api/app/services/claude_runner.py`、新增 `apps/api/app/services/session_tasks.py`、`apps/api/app/routers/sessions.py`、`apps/web/src/App.tsx`

## 背景

Agent 的后台任务（`Bash(run_in_background=True)`、长期 monitor、后台子 Agent）可能在当前 turn 结束后继续运行。现状：

- 后端事件流 `TurnStream` 按 turn 创建，`turn_end` 后 `close()`；之后的 `task_*` 事件无法再 `publish`。
- SDK 的 `ClaudeSDKClient.receive_messages()` 会把轮末后的事件留在消息缓冲里；平台只在下一轮读取时才发布，client 在空闲 TTL 内被逐出则丢失。
- 前端 `turn_end` 后 `finishTurn` 刷新历史，`refreshTranscript` 在非流式时把 `tasks` 清空，所以任务面板随本轮回答一起消失。
- `running_sessions` 目前只是 turn 租约：行存在表示 session 有一个 turn 在跑，turn 结束就删除；启动恢复把每一行当作未完成的 turn 去 resume。

## 目标

- turn 结束后，后台任务的 `task_started` / `task_progress` / `task_updated` / `task_notification` 仍能实时送到前端。
- 前端新增「会话级任务区」，跨 turn 保留；刷新/重连用快照恢复；服务重启后显示「已中断」而不是假装还在运行。
- 任务运行期间不因 5 分钟空闲 TTL 被逐出；每个任务从 `started_at` 起最多保活 10 分钟，到任务结束为止。被逐出或重启时准确标记中断。
- 不新增数据库表；任务状态扩展现有 `running_sessions`。

## 非目标

- 不把任务写入 transcript 或历史消息；任务区不是历史记录。
- 不提供任务取消、任务详情页、跨会话任务总览。
- 不改变 `TurnStream` 的 turn 生命周期和现有聊天流式协议。
- 不做多进程/多实例；平台仍是单进程。
- 不把后台任务状态同步到微信等渠道。

## 现有约束

- SDK 任务终态集合：`completed`、`failed`、`stopped`、`killed`（`claude_agent_sdk.types.TERMINAL_TASK_STATUSES`）。终态可能只通过 `task_updated` 到达，不能只依赖 `task_notification`。
- SDK 在 result 之后为后台任务保活最多约 10 分钟（`DEFAULT_RUN_END_CEILING_MS = 600_000`）。
- 平台 SDK client 空闲 TTL 默认 300 秒，缓存上限 8 个；逐出会 `disconnect()` CLI，后台任务随之中断。
- `TurnStream` 是内存流，事件带递增索引，`turn_end` 后关闭。
- `running_sessions` 当前只有 `session_id` 主键；启动恢复 `recovery.recover_running_sessions` 读取所有行并提交 `source="resume"` 的 turn。

## 设计

### 1. 数据模型：扩展 `running_sessions`

`RunningSession` 增加：

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `has_turn` | BOOLEAN | `1` | `1` = 有 turn 在跑；`0` = 只有后台任务或已完成任务保留 |
| `tasks_json` | TEXT | `'[]'` | 任务快照数组，最大 64 KB |
| `updated_at` | UTCDateTime | 当前时间 | 最近一次 turn 或任务事件写入时间 |

任务对象：

```json
{
  "task_id": "task-1",
  "tool_use_id": "toolu_01...",
  "description": "回测 BTC 1h",
  "status": "running",
  "summary": "",
  "last_tool_name": "Bash",
  "usage": {"total_tokens": 1234, "tool_uses": 2, "duration_ms": 45000},
  "started_at": "2026-11-16T08:00:00+00:00",
  "updated_at": "2026-11-16T08:00:45+00:00",
  "completed_at": null
}
```

迁移（`init_db` 中，SQLite）：

1. `PRAGMA table_info(running_sessions)`。
2. 缺列时执行：
   - `ALTER TABLE running_sessions ADD COLUMN has_turn INTEGER NOT NULL DEFAULT 1`
   - `ALTER TABLE running_sessions ADD COLUMN tasks_json TEXT NOT NULL DEFAULT '[]'`
   - `ALTER TABLE running_sessions ADD COLUMN updated_at DATETIME`
3. 现有行 `has_turn=1`，恢复行为不变；`updated_at` 回填为当前时间。
4. 新库由 `Base.metadata.create_all` 直接建出新列。
5. 服务在收到任务事件但行不存在时创建行，显式写 `has_turn=0`；不要依赖数据库列默认值 `1`。

写入策略：

- `task_started`、终态 `task_updated` / `task_notification`：立即写库。
- `task_progress`：内存即时广播，写库做 1 秒防抖；turn 结束、client 逐出、进程关机前强制 flush。
- 写库失败只记日志，不影响 turn 和实时广播；下一事件或 flush 重试。

### 2. 生命周期

**turn 开始**

- `turn_lease.begin_turn(session_id)`：插入或更新行，`has_turn=1`、`updated_at=now`；保留已有 `tasks_json`（上一轮的后台任务可能还在跑）。

**turn 结束**

- `claude_runner.py` 的 turn 结束路径把 `turn_lease.remove(session_id)` 改为 `turn_lease.end_turn(session_id)`：`has_turn=0`、`updated_at=now`。`recovery._drop_lease` 等「彻底丢弃租约」的路径仍调用 `remove()` 删除行。
- 若 `tasks_json` 为空：直接删除行（与当前行为一致）。
- 若还有任务（运行中或 10 分钟内完成）：保留行，由清理任务处理。

**后台任务结束**

- 终态事件更新 `tasks_json`，`completed_at=now`。
- 若 `has_turn=0` 且所有任务都已终态：行保留到最后一个终态的 10 分钟保留期结束。

**启动恢复**

- `turn_lease.list_turn_leases()` 只返回 `has_turn=1` 的行；`recover_running_sessions` 用它恢复 turn。
- 启动时 `SessionTaskService.startup()`：
  1. 读取所有 `running_sessions` 行。
  2. 把所有非终态任务改为 `status="interrupted"`、`summary="服务重启，任务已中断"`、`completed_at=now`。
  3. `updated_at=now`，保留 10 分钟。
  4. `has_turn=1` 的行随后由恢复流程 resume；`has_turn=0` 的行不 resume。
- `is_running(session_id)` 继续表示「有 turn 在跑」= `has_turn=1`；新增 `has_active_tasks(session_id)` 供保活判断。`recovery._answer_orphan_deliveries` 仍用 `is_running` 判断交付行是否有仍在跑的 turn，语义不变。

**清理**

- 每 60 秒执行一次：
  - 终态任务超过 10 分钟：从 `tasks_json` 移除，并发 `task_removed`。
  - `has_turn=0`、`tasks_json` 为空、`updated_at` 超过 10 分钟：删除行。
  - `has_turn=0`、仍有非终态任务、最晚 `started_at + 10 分钟` 已过：统一标记 `interrupted`（原因「后台任务超过保活上限」），再从保留期开始计算；避免运行中任务永久占行。保活上限按任务开始时间计算，不因 `task_progress` 刷新而延长。
  - `has_turn=1` 的行永不由清理任务删除。
- 单会话最多保留 32 个任务；超出时先丢最旧的终态任务，再丢更旧的终态任务直到满足上限。运行中任务不因 UI 上限被强制标记中断；若 32 个任务全部在运行，保留全部运行中任务，只裁剪终态任务。`tasks_json` 超过 64 KB 时同样优先裁剪终态条目。

### 3. 任务事件与状态合并

`SessionTaskService.publish(session_id, event)` 接收 `sdk_events.task_event()` 的映射结果：

- `task_started`：创建或覆盖任务，`status="running"`，记录 `started_at`。
- `task_progress`：更新 `description`、`last_tool_name`、`usage`、`updated_at`；不改变终态。
- `task_updated`：更新 `status`；若在 `TERMINAL_TASK_STATUSES` 中，标记 `completed_at`，并从活动计数移除。
- `task_notification`：更新 `status`、`summary`、`usage`；终态时标记 `completed_at`。
- 同一 `task_id` 的事件幂等合并；未知任务收到 progress/notification 时按 `running` 新建，避免丢事件。

终态统一为：

- `completed` / `failed` / `stopped` / `killed`：SDK 原样保留。
- `interrupted`：平台在 client 逐出、进程重启或任务被裁剪时写入，`summary` 说明原因。

### 4. 实时通道

新增进程内 `SessionTaskService`：

- `publish(session_id, event)`：更新内存任务状态、广播给订阅者、标记脏数据。
- `snapshot(session_id)`：从 `running_sessions.tasks_json` 读取并返回任务列表。
- `subscribe(session_id)` / `unsubscribe`：返回 `asyncio.Queue`；广播只发给当前订阅者。
- `mark_interrupted(session_id, reason)`：把非终态任务改为 `interrupted`，广播并写库。
- `keep_alive_until(session_id)`：返回运行中任务中最晚 `started_at + 10 分钟` 的时间；无运行中任务返回 `None`。进度事件不延长上限。
- `startup()`、`start()`、`stop()`：启动时标记中断、启动清理循环、关机前 flush。

API：

`GET /api/sessions/{session_id}/tasks`

```json
{
  "has_turn": true,
  "updated_at": "2026-11-16T08:00:45+00:00",
  "tasks": [
    {
      "task_id": "task-1",
      "tool_use_id": "toolu_01...",
      "description": "回测 BTC 1h",
      "status": "running",
      "summary": "",
      "last_tool_name": "Bash",
      "usage": {"total_tokens": 1234, "tool_uses": 2, "duration_ms": 45000},
      "started_at": "2026-11-16T08:00:00+00:00",
      "updated_at": "2026-11-16T08:00:45+00:00",
      "completed_at": null
    }
  ]
}
```

- 没有行时返回 `{"has_turn": false, "updated_at": null, "tasks": []}`。
- 只返回已保留的任务；终态超过 10 分钟的任务不再返回。

`GET /api/sessions/{session_id}/events`

- SSE，`Content-Type: text/event-stream`，每 15 秒发送 `: ping`。
- 连接后第一条事件固定为 `tasks_snapshot`，含当前 `tasks` 与 `has_turn`；即使没有任务也发送空快照，用于清空前端旧状态。随后推送实时事件。
- 事件类型复用现有 `TurnStreamEvent` 的任务形状：
  - `{"type":"tasks_snapshot","tasks":[...],"has_turn":true}`
  - `{"type":"task_started",...}`
  - `{"type":"task_progress",...}`
  - `{"type":"task_updated",...}`
  - `{"type":"task_notification",...}`
  - `{"type":"task_removed","task_id":"..."}`
- 断线重连不需要 `after` 序号：每次连接先拿快照，再接收后续事件。实现顺序：
  1. 先把订阅者注册到内存 hub。
  2. 再读 `running_sessions.tasks_json` 生成 `tasks_snapshot`。
  3. 订阅后产生的实时事件进入队列，快照发送后按序追加。
  4. 若某个事件同时落在快照和队列里，前端按 `task_id` 幂等合并，重复应用无副作用。
- 认证与现有会话接口一致，使用 cookie。

现有 turn SSE 保留任务事件，不改协议；前端任务区只消费会话级通道。若会话级通道短暂不可用，turn SSE 的任务事件可作为兜底写入同一任务 map（按 `task_id` 幂等合并），但 turn 结束后仍以会话级通道为准。

### 5. Runner 集成

- `ClaudeTurnRunner` 增加 `session_tasks: SessionTaskService | None` 构造参数；`main.py` 在创建 runner 前创建服务并传入。
- 在 `run()` 读取 `mapped_task` 的位置：
  ```python
  mapped_task = task_event(message)
  if mapped_task is not None:
      stream.publish(mapped_task)
      session_tasks.publish(turn.session_id, mapped_task)
      continue
  ```
- `session_tasks.publish` 同步更新内存并广播；写库由服务内部的防抖 writer 完成，不阻塞消息循环。
- 空闲清扫 `sweep_idle_clients()`：
  - 候选 client 若 `session_tasks.keep_alive_until(session_id)` 仍晚于当前时间，跳过逐出。
  - 超过 10 分钟上限的任务由服务标记 `interrupted`，随后允许逐出。
- `_evict_key()` / `_disconnect()` 成功或失败断开后，调用 `session_tasks.mark_interrupted(session_id, "SDK client 已断开")`；best-effort，失败只记日志。
- 容量淘汰 `_enforce_capacity()`：
  - 优先跳过有运行中任务的 client。
  - 允许缓存数临时超过上限最多 2 个；超过 `max_clients + 2` 时淘汰最旧的活动任务 client，并标记其任务中断。
- `main.py` 启动顺序：
  1. `init_db()`（含迁移）。
  2. 创建 `SessionTaskService`，`await service.startup()`（标记重启中断）。
  3. `recover_running_sessions`（只恢复 `has_turn=1`）。
  4. 启动 service 的清理循环。
- 关机顺序：`registry.close()` → `runner.close_all()`（逐出 client 时标记任务中断）→ `session_tasks.stop()`（停止清理循环并 flush）。`session_tasks.stop()` 放在 `runner.close_all()` 之后，确保最后的任务状态已经写入。

### 6. 前端

**状态**

- 新增 `tasksBySession: Record<string, Record<string, LiveTask>>`（或等价的 reducer），与现有 `live.tasks` 解耦。
- `LiveTask` 增加 `startedAt`、`updatedAt`、`completedAt`（可选）；展示仍用现有字段。服务端 JSON 是 snake_case，前端用一个 `taskFromEvent()` 映射函数统一转成现有 camelCase 结构。

**订阅**

- 当前会话变化时打开 `EventSource("/api/sessions/{id}/events")`，切换会话时关闭；与 turn 的 `openStream` 独立。
- 第一条 `tasks_snapshot` 覆盖该会话的 task map；后续事件按 `task_id` 合并；`task_removed` 删除。
- 断线后指数退避重连，重连成功先应用快照；页面隐藏时保持连接（与现有 SSE 行为一致）。
- 会话切换回来时先显示内存中的旧 map，再用快照刷新。

**展示**

- 输入框上方新增 `SessionTaskPanel`：
  - 无任务时不渲染。
  - 折叠标题显示「后台任务 N」，有运行中任务时显示运行中数量。
  - 展开后按「运行中优先，其次最近更新」排序；每行显示描述、状态、最后工具、完成摘要。
  - 状态颜色复用现有 `taskStatusClass`；`interrupted` 使用警告色。
- 每轮「执行过程」`LiveProcess` 移除 `tasks` 入参和任务列表，只保留思考、工具、子 Agent。
- `FrozenLive` 移除 `tasks` 字段（停止时冻结的实时内容不再包含任务）；相关渲染与类型同步删除。
- `applyStreamEvent` 中的 `task_*` 分支改为写入 `tasksBySession`（兜底），不再写 `live.tasks`。保留这些分支：会话级 SSE 短暂不可用时，当前 turn 的任务仍能更新；turn 结束后以会话级通道为准。
- turn_end、新 turn、`refreshTranscript` 都不清空 `tasksBySession`；只有 `tasks_snapshot`、`task_removed` 和清理事件会修改。

### 7. 错误处理

- 写库失败：日志 + 保留内存状态；实时广播继续；下一次 flush 重试。
- SSE 断开：前端退避重连；重连快照覆盖；不重复追加。
- 多标签页：每个标签页独立 SSE；服务端广播给所有订阅者；状态幂等。
- 会话删除：删除会话时同时删除该 session 的 `running_sessions` 行并关闭订阅者；实现上在 `session_catalog.delete()` 的平台清理步骤里调用 `turn_lease.remove(session_id)` 和 `session_tasks.forget(session_id)`。
- 服务重启：`startup()` 标记中断；`has_turn=1` 的行由 recovery 恢复；`has_turn=0` 的行保留 10 分钟后清理。
- DB 行不存在但收到任务事件：`publish` 创建行（`has_turn=0`，`tasks_json` 写入）；避免丢事件。
- `tasks_json` 解析失败：视为空列表，记 warning，下一次写入覆盖。

## 测试

- `test_turn_lease.py`（扩展/新建）：迁移列、`begin_turn`/`end_turn`、`list_turn_leases` 只返回 `has_turn=1`、`is_running` 语义、保留行清理、旧行补列后 `has_turn=1`。
- `test_session_tasks.py`（新建）：四类事件合并、终态集合、`interrupted`、快照、订阅广播、`task_removed`、32 任务/64 KB 上限、10 分钟保留、`keep_alive_until`。
- `test_claude_runner.py`：任务事件同时进 turn stream 和 service；有活动任务时 `sweep_idle_clients` 不逐出；超过 10 分钟可逐出并标记中断；容量淘汰跳过活动 client。
- `test_recovery.py`：只恢复 `has_turn=1`；`has_turn=0` 不提交 resume；启动标记中断。
- `test_chat_api.py` / `test_sessions_api.py`：`GET /tasks` 快照、SSE 首帧 `tasks_snapshot`、`task_*`、`task_removed`、认证、无行时为空。
- 前端：`npm run build`；手工验证任务区在 turn_end 后继续更新、刷新恢复、10 分钟移除、重启显示中断。
- 全量：`.venv/bin/python -m pytest apps/api/app/tests`。

## 验收标准

- 启动一个超过当前 turn 的后台任务：turn 结束后任务区仍显示「运行中」，完成/失败/停止时实时更新。
- 页面刷新或断线重连后，运行中的任务通过快照恢复；已完成任务在 10 分钟内仍可见，之后消失。
- 服务重启：正在运行的任务显示「已中断」，保留 10 分钟；`has_turn=1` 的 turn 仍按原恢复流程续跑。
- 有运行中任务的 client 不被 5 分钟空闲 TTL 逐出；最多保活 10 分钟；逐出后任务标记「已中断」。
- 每轮「执行过程」不再显示任务，任务只在会话级任务区出现，无重复渲染。
- 任务事件不写入 transcript；刷新历史不恢复任务。
- 现有 turn SSE、聊天流式、恢复、渠道投递测试全部通过；前端构建通过。

## 风险与取舍

- **复用 `running_sessions`**：表名与「turn 租约」语义不再完全一致；靠 `has_turn` 过滤保持恢复安全。所有读写 `running_sessions` 的代码必须显式区分 turn 与任务行，实施计划要覆盖 `turn_lease`、`recovery`、`session_cleanup`、测试。
- **SQLite 写放大**：`task_progress` 可能频繁；用 1 秒防抖和终态立即写控制。单进程 + WAL + busy_timeout，预期可接受。
- **JSON 列**：不支持按任务字段查询；本功能只需要按会话读取快照，够用。64 KB/32 任务上限防止膨胀。
- **10 分钟保活**：后台任务可能被杀；UI 明确标记「已中断」，不假装完成。长期任务需要用户重新发起。
- **重启后的中断任务**：占用 `running_sessions` 行 10 分钟；清理任务必须只删 `has_turn=0` 的行，避免误删恢复租约。
- **会话级 SSE 与 turn SSE 并存**：两条连接增加少量开销；任务事件幂等合并，避免重复 UI。

## 文档同步

实施时：

- `docs/intent.md` 追加 F26（会话级后台任务事件），编号水位更新。
- `CLAUDE.md` 更新插件/聊天段落的运行约定：`running_sessions` 的 `has_turn` 语义、任务区、保活和重启中断。
- `README.md` 更新聊天/运行时说明与验收步骤。
- 实施时同步修正 F2 第 3/5 条与 F16 已知能力损失中关于后台任务、刷新恢复和 `running_sessions` 恢复范围的旧描述。
