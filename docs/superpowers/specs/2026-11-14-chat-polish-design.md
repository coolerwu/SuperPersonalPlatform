# 聊天体验打磨设计

- 日期：2026-11-14
- 状态：待评审
- 范围：`apps/web` 聊天交互 + `apps/api` 少量执行核与流协议改动。不改权限模型、不改视觉风格、不新增依赖。

## 背景

最近完成了聊天跟随、渲染隔离、长列表虚拟化、页面分包与数据缓存（F21）。本轮继续打磨使用中最明显的四处摩擦：

1. 运行中发送的追问会排队，但只能看到一句全局提示；看不到自己排在第几，也不能只撤回其中一条，只能 `/stop` 把当前和全部排队一起停掉。
2. SSE 断线时静默关闭连接并刷新历史，实时文本会被清掉；服务端订阅会重放整段缓冲，直接重连会把相同增量追加两次。
3. 点停止后，已经生成的半截回复会随实时状态一起消失；如果历史里没有落盘，就彻底看不到。
4. 切到别的会话或标签页后，轮次结束没有任何提示，侧栏也不留痕迹；侧栏会话标题没有改名入口（后端接口已存在但前端未接）。

代码现状（可核实）：

- 排队与轮次：`apps/web/src/App.tsx` 的 `send()`、`finishTurn()`、`queuedTurnsRef`、`openStream` 系列；队列实体在 `apps/api/app/services/session_registry.py` 的 `asyncio.Queue` 中，`interrupt()` 只能整体 drain。
- 流：`apps/api/app/services/turn_stream.py` 的 `subscribe()` 无条件回放整个 `_buffer`；`apps/api/app/routers/sessions.py` 的 `stream_session()` 不输出事件 id。
- 停止：`App.tsx` 的 `cancel()` 关闭流后 600ms 触发 `refreshTranscript()`；`refreshTranscript()` 在非流式时清空 `streamText` 等实时字段。
- 未读：`Sidebar.tsx` 只渲染 `busy` 与时间；`finishTurn()` 结束后没有跨会话状态。
- 改名：`POST /api/sessions/{session_id}/rename` 与 `session_catalog.rename()` 已存在；`apps/web/src/api.ts` 没有对应方法，侧栏没有入口。

## 目标与非目标

### 目标

1. 排队中的追问显示序号，可单条撤回，其余按原顺序执行。
2. SSE 断线自动重连并按事件序号续传，不重复、不清空；断线期间有明确提示。
3. 停止后保留已经生成的半截回复，历史落盘后与历史行收敛。
4. 非当前会话或页面不可见时，轮次完成/失败在侧栏与标签标题留未读提示，查看后清除。
5. 侧栏支持用弹窗重命名会话，保存后侧栏与顶栏同步更新。

### 非目标

- 不做 HILP 审批或权限模型改动（仍是 `bypassPermissions`）。
- 不新增执行过程详情（工具输入/输出/耗时）展示。
- 不做系统通知、消息编辑重发、单条失败重试、消息时间戳、会话全文搜索。
- 不引入状态管理库、前端测试框架，不拆分 `App.tsx` 的路由结构。
- 未读状态不持久化，刷新页面或换浏览器即丢失。

## 设计

### 1. 排队追问：序号与单条撤回

**后端**

`SessionRegistry` 新增：

```python
async def cancel_queued(self, session_id: str, turn_id: str) -> bool
```

行为：

- 先检查 `_pending`：如果该会话的 pending turn 就是目标 turn，且会话不在 `_started` 中，则把 turn id 加入 `_cancelled`，向它的流发布带 `scope: "turn"` 的 cancelled `turn_end` 并 `close()`，然后丢弃这条 turn。
- 否则把该会话的 `asyncio.Queue` 一次性 drain 到列表，按 `turn.id` 找到目标项并移除，其余项按原顺序放回；被移除项的流发布带 `scope: "turn"` 的 cancelled `turn_end`（与 `/stop` 的会话级取消区分）并关闭。drain 时每个取出的项都调用一次 `task_done()`，放回的项会重新增加未完成计数，净效果是取消项 −1。
- drain 与放回之间不能有 `await`，保持与 worker 退休逻辑的原子性；投递义务的清理（`_discard_delivery`）在所有无等待操作完成后再执行。
- 目标 turn 已经开始执行时返回 `False`，不影响正在执行的 turn。
- 移除后如果队列为空、没有运行中或收尾中的 turn，沿用 `interrupt()` 的 worker 退休逻辑；否则交给现有 worker 在队列耗尽后自行退休。

路由：

```
POST /api/sessions/{session_id}/turns/{turn_id}/cancel
→ 200 {"cancelled": true | false}
```

运行时未就绪返回 503；找不到排队项返回 `{"cancelled": false}`，不返回 404，便于前端把「不可撤回」与「接口失败」分开。

**前端**

- `MessageList` 里的排队用户气泡新增两种信息：右上角「排队中 2/3」标签，和行内「×」撤回按钮。序号按 `queuedMessages` 的提交顺序计算，只统计 `queued === true` 的项；`turnId` 还没返回的项也占序号，但按钮禁用并显示「正在提交…」。
- App 新增 `cancelQueued(sessionId, messageId)`：找到 pending 项与 `turnId`，调用接口。成功时从 pending 列表移除该条、从 `queuedTurnsRef` 移除对应 turn id；返回 `false` 时触发一次 `refreshTranscript()`，并把 notice 设为「这条已经开始或已经结束，队列已刷新」；请求失败时保留气泡并显示错误。
- 输入区排队提示改成带数量：「已排队 2 条，前面的任务完成后依次执行」；没有排队时回到现有文案。
- `/stop` 语义不变：取消正在执行与全部排队。被取消的排队 turn 仍通过现有 `turn_end(cancelled)` 路径清理。
- `queuedMessages` 的渲染参数由 App 传入：`queueIndex`、`queueTotal`、`onCancelQueued`。

**测试**

- `test_session_registry.py`：单条撤回后其余 turn 顺序不变；被撤回流收到 cancelled 的 `turn_end`；投递行被丢弃；运行中的 turn 不受影响；pending 未启动路径可撤回。
- `test_chat_api.py`：路由返回 `{"cancelled": bool}`；运行时未就绪 503。

### 2. SSE 断线续传与停止保留半截回复

**服务端：事件序号**

- `TurnStream` 用缓冲下标作为事件序号。`publish()` 把 `(index, event)` 放入每个订阅者队列；`subscribe(after=0)` 只回放 `_buffer[after:]` 并带上各自下标；`close()` 合成的 `turn_end` 使用 `len(_buffer)` 作为下标。`after` 先夹到 `[0, len(_buffer)]`，防止越界。
- `GET /api/sessions/{id}/stream` 新增 `after` 查询参数，默认 0（保持现有全量回放行为）；每条事件输出 `id: {index}`，然后 `data: {...}`。浏览器 `EventSource` 会把 id 放进 `event.lastEventId`。
- `subscribe()` 的返回类型从 `Queue[dict]` 变为 `Queue[tuple[int, dict]]`，仅流路由使用，测试同步更新。
- 新增轻量探测接口 `GET /api/sessions/{id}/turn?turn_id=...`，返回 `{"turn_id": <当前轮 id 或 null>, "busy": bool, "alive": bool}`。`alive` 表示请求里给出的 turn 的流是否还能订阅；不传 `turn_id` 时为 false。`EventSource` 读不到 HTTP 状态码，重连前先用这个普通 JSON 请求判断旧轮是否还活着，避免对已经结束的轮无限重连，也不会把排队中的轮误判为已结束。

**前端：自动续传**

- 每个会话维护 `lastEventIndex`，订阅新 turn 时清零；`openStream(forSession, turnId, after)` 在 `after > 0` 时把参数拼进 URL。
- `source.onmessage` 解析 `event.lastEventId` 更新 `lastEventIndex`，再走现有 `applyStreamEvent()`。
- `source.onerror` 不再静默，先探测再决定：
  - 请求 `GET /api/sessions/{id}/turn?turn_id=<当前轮>`。探测失败（网络仍未恢复）：notice 显示「连接中断，正在重连…」，退避 500ms → 1s → 2s → 4s，之后封顶 5s，最多约 8 次后重新探测。
  - 探测成功且 `alive` 为真：用同一个 turn id 和 `lastEventIndex + 1` 重连。
  - 探测成功但 `alive` 为假：该轮已经结束；关闭连接并刷新历史。`busy` 为真时按无 turn id 模式 attach 当前轮。
  - 不知道 turn id 的 attach 模式：`busy` 为真时带退避重新 attach；否则刷新历史。
  - `onopen` 成功后清掉重连 notice；连接已经恢复，即使暂时没有新数据也不该继续显示「正在重连」。
  - 探测重试耗尽仍未恢复：调用 `refreshTranscript()` 并显示「连接已断开，已刷新为最新历史」；后续轮询发现会话仍忙碌时会再次 attach。中途不刷新，避免清掉实时文本。
- 同会话始终保持单个 `EventSource`：每次重试先关闭旧实例，再按 after 接上，避免重复追加。

**前端：停止后保留半截回复**

- `SessionLive` 新增 `frozen` 快照：`{text, reasoning, activity, subagents, tasks, artifacts}`，初始为 `null`。
- `cancel()` 在中断接口返回 `interrupted: true` 后，用一次 `updateLive()` 原子地完成：把当前实时字段复制到 `frozen`、`tracking` 置 false；随后关闭流。返回 `false` 时说明没有可中断的轮次，不冻结，走现有「当前没有正在执行的任务」提示。
- `refreshTranscript()` 拿到新历史后判断：从列表末尾向前找最后一条 user 消息，如果其后存在非空 content 的 assistant 消息，说明半截回复已落盘，丢弃 frozen；否则保留 frozen，渲染成带「已停止」标记的行（无光标、不显示「正在生成…」）。判断和消息替换在同一次状态更新里完成，避免同一帧出现历史行与 frozen 行。
- frozen 在下一次在该会话发送或重新打开该会话（`openSession`）时清空；如果停止时还有幸存的排队 turn，已停止行保留在新 turn 的流式行上方，直到历史确认半截回复已落盘或用户下次发送。
- 停止时如果还有未被取消的排队追问，下一条 turn 开始前先把 `streamText`、`streamReasoning`、`activity`、`subagents`、`tasks`、`artifacts` 清空再订阅。这同时修掉一个现有缺陷：当前 `/stop` 后幸存队列直接开新流，新 turn 的增量会追加在上一轮的半截文本后面。

**验收**

- DevTools 离线 3–5 秒再恢复：出现重连提示，恢复后文本不重复、不缺字。
- `/stop` 后历史有半截回复则显示历史行；没有则显示「已停止」的 frozen 行。有幸存排队追问时，新 turn 从干净的气泡开始。
- `test_turn_stream.py`：`subscribe(after)` 只回放尾部；序号单调且与缓冲对齐；关闭后仍恰好一个 `turn_end`；多订阅者序号一致。
- 流路由测试断言 `id:` 行与 `after` 行为；轮次探测接口在空闲（`null` / `false`）、运行中（运行 turn id / `true`）、仅排队（`null` / `true`）三种状态下返回正确值。

### 3. 完成未读提醒

- App 新增 `unreadBySession: Record<string, "done" | "failed">`，只存在内存。
- 记录时机在 `finishTurn()` 结束、会话真正进入空闲之后：状态为 `completed` 或 `failed`，且满足以下任一条件时记未读：
  - 该会话不是当前会话；
  - 页面不可见（`document.visibilityState === "hidden"`）或窗口没有焦点（`document.hasFocus() === false`）。
- `cancelled` 不记未读；失败仍保留现有 notice。
- 侧栏会话行在标题前显示未读点：成功薄荷色、失败玫红色，`title` 分别为「已完成」「执行失败」，并带 `aria-label`。未读点与忙碌标记可以同时存在。
- 标签标题按当前 Agent 会话列表中的未读条数显示 `(2) Agent Command Center`；没有未读时恢复 `Agent Command Center`。切到其他 Agent 时旧 Agent 的未读不显示，切回来仍在。
- 清除规则：打开该会话且页面可见；窗口重新获得焦点时清除当前会话的未读；侧栏轮询不清除。
- 刷新页面或换浏览器丢失未读，这是明确边界。

### 4. 侧栏会话改名

- `apps/web/src/api.ts` 新增 `renameSession(sessionId, agentId, title)`，调用现有 `POST /api/sessions/{id}/rename?agent_id=...`。
- 侧栏行悬停时在删除 × 旁显示铅笔按钮，点击打开「重命名会话」弹窗（结构沿用 `NewAgentDialog` 的 `modal-backdrop` / `modal` 样式）：预填当前标题、自动聚焦并全选、Enter 保存、Esc 或点遮罩取消、保存中禁用按钮、失败在弹窗内显示错误。铅笔按钮在行悬停、该行选中或键盘聚焦时可见，`@media (hover: none)` 下常驻，保证触屏可以改名。
- 标题去首尾空格；空标题或与原来相同则禁用保存；最长 80 字，与 `session_catalog._TITLE_LIMIT` 一致。
- 保存成功后先本地更新 `sessions` 中对应项再关闭弹窗，并调用 `refreshSessions()` 对齐服务端；侧栏、聊天顶栏、移动端顶栏都会立即反映本地更新，刷新只做服务端对齐。失败保留弹窗。
- 聊天顶栏不加入口，改名只在侧栏。

**验收**

- 在会话 B 里查看时会话 A 完成：A 出现未读点、标签标题变为 `(1)`；点开 A 后两者消失。
- 当前会话执行中把标签页切走，完成后同样出现未读；切回浏览器后当前会话的未读清除。
- 侧栏改名后立即反映在侧栏与顶栏，刷新页面仍是新标题；空标题不能保存。

## 涉及文件

### 后端

- `apps/api/app/services/session_registry.py`：`cancel_queued()`；复用 `_discard_delivery()` 与 worker 退休逻辑。
- `apps/api/app/services/turn_stream.py`：事件序号、`subscribe(after)`、关闭时序号。
- `apps/api/app/routers/sessions.py`：流接口 `after` 参数与 `id:` 输出；取消排队路由；轮次探测接口。
- 测试：`test_session_registry.py`、`test_turn_stream.py`、`test_chat_api.py`。

### 前端

- `apps/web/src/App.tsx`：`cancelQueued()`、`lastEventIndex` 与重连状态机、`cancel()` 冻结、`refreshTranscript()` 收敛、`unreadBySession`、标题与清除逻辑、改名弹窗状态。
- `apps/web/src/components/ChatPanel.tsx` / `MessageList.tsx`：排队序号、撤回按钮、带数量的排队提示、frozen 停止行。
- `apps/web/src/components/Sidebar.tsx`：未读点、铅笔按钮。
- 新增 `apps/web/src/components/RenameSessionDialog.tsx`。
- `apps/web/src/api.ts`：`renameSession`、取消排队接口、轮次探测接口、流 URL 的 after 参数。
- `apps/web/src/types.ts`：`SessionLive.frozen`、排队渲染参数类型。

## 兼容与迁移

- 新接口与 `after` 参数都是加法式改动：旧客户端不传 `after` 仍得到全量回放；取消排队路由是新增路径。
- 无数据库 schema 变更，无数据迁移，无新依赖。
- 需求需要先写入 `docs/intent.md`（新增 F22），再进入实现；实现完成后同步 `CLAUDE.md` 与 `README.md`。

## 实施顺序

1. `docs/intent.md` 追加 F22。
2. 后端：`cancel_queued` + 路由 + 测试。
3. 后端：`TurnStream` 序号 + 流路由 `after` + 测试。
4. 前端：排队序号与单条撤回。
5. 前端：SSE 续传状态机。
6. 前端：停止冻结与收敛。
7. 前端：未读提醒。
8. 前端：会话改名。
9. 前端构建、后端全量测试、手工验收清单，按项目约定做 3 次自我 code review。

每一步可独立提交；第 4–6 步共用 `App.tsx` 的实时状态，需要按顺序落地，避免互相覆盖。

## 风险

- **事件序号与重放**：`subscribe(after)` 依赖缓冲下标稳定；缓冲只在轮次存活期内保留，轮次结束后订阅者拿不到旧下标。当前设计只在同一轮内续传，符合现状（进程重启或轮次结束本来就只能从历史重建）。
- **撤回竞争**：worker 可能在取消请求到达的同时出队。`cancel_queued` 只处理仍可取消的排队项；已经把 turn 标记为 cancelled 后，runner 的取消检查保证它不会真正执行。
- **重连风暴**：退避与次数上限避免服务端异常时无限重连；每次重连都替换旧 EventSource。
- **后台标签页**：浏览器可能节流后台页面的计时器，重连会晚一些；服务端 ping 与历史刷新兜底，不丢最终状态。
- **frozen 内存**：每个会话只保留最后一份停止快照，下一次发送或重新打开该会话即释放。
- **未读判定**：`document.hasFocus()` 在开发者工具聚焦等场景可能为 false，会多记一次未读；清除规则保证回到会话即消失。
- **改名与轮询**：乐观更新后紧接 `refreshSessions()`；若会话已被删除，弹窗显示错误并刷新列表。
