# 聊天体验打磨实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让排队追问可撤回、SSE 断线可续传、停止后保留半截回复、会话完成有未读提示、侧栏可重命名会话。

**Architecture:** 后端为会话队列增加按 turn 取消、为 `TurnStream` 增加事件序号与 `after` 回放、增加轮次探测接口；前端在 `App.tsx` 的实时状态机里接入续传与冻结逻辑，并把排队信息、未读点、改名入口拆到现有组件。所有接口改动都是加法式，无数据库变更。

**Tech Stack:** FastAPI + asyncio（Python 3.12）、React 18 + TypeScript + Vite、pytest。

**依据设计：** `docs/superpowers/specs/2026-11-14-chat-polish-design.md`

---

## 文件结构

### 后端

- `apps/api/app/services/turn_stream.py`：流缓冲与订阅，新增事件序号和 `subscribe(after)`。
- `apps/api/app/services/session_registry.py`：会话队列，新增 `cancel_queued()`。
- `apps/api/app/routers/sessions.py`：新增 `after`/`id:` 流输出、轮次探测、取消排队路由。
- `apps/api/app/tests/test_turn_stream.py`、`test_session_registry.py`、`test_chat_api.py`：对应测试。

### 前端

- `apps/web/src/api.ts`：新增 `cancelQueuedTurn`、`getSessionTurn`、`renameSession`，流 URL 支持 `after`。
- `apps/web/src/types.ts`：新增 `FrozenLive` 类型。
- `apps/web/src/App.tsx`：排队撤回、续传状态机、停止冻结、未读、改名弹窗状态。
- `apps/web/src/components/ChatPanel.tsx`：透传排队元数据、冻结快照。
- `apps/web/src/components/MessageList.tsx`：排队标签/撤回按钮、停止行渲染。
- `apps/web/src/components/Sidebar.tsx`：未读点、重命名按钮。
- `apps/web/src/components/RenameSessionDialog.tsx`：新建重命名弹窗。
- `apps/web/src/styles.css`：排队标签、停止标记、未读点、重命名按钮样式。

---

## Task 1: 需求记录 F22

**Files:**
- Modify: `docs/intent.md`（追加到 F21 之后）

- [ ] **Step 1: 在 `docs/intent.md` 末尾追加 F22**

在文件最后（F21 小节之后）追加：

```markdown
### F22 聊天体验打磨

- 状态：现行
- 日期：2026-11-14
- 来源：HILP 讨论后的聊天细节打磨

#### 功能点

1. 排队追问：运行中发送的追问显示「排队中 N/M」与撤回按钮；撤回单条不影响正在执行的 turn 和其他排队项；后端提供按 turn id 取消排队项的接口；输入区排队提示显示数量。
2. SSE 续传：流事件带递增序号；客户端断线后先探测会话当前轮次，再按序号续传；重连有提示，不重复、不清空已显示内容；重连失败时回落到历史刷新。
3. 停止保留半截回复：点停止后冻结当前实时内容；历史里已有对应 assistant 消息则收敛为历史行，否则以「已停止」行保留到下次发送或重新打开会话。
4. 完成未读：非当前会话或页面不可见时的轮次完成/失败，在侧栏会话行显示未读点（成功绿色、失败红色），标签标题显示未读数量；打开会话或窗口重新聚焦后清除。未读只存在内存。
5. 会话改名：侧栏会话行提供重命名入口与弹窗，调用现有会话重命名接口，保存后侧栏与顶栏同步更新。

#### 验收标准

- 运行中发三条追问，撤回第二条后第一、第三条按原顺序执行。
- 断网 3–5 秒恢复后流式文本不重复、不缺字；`/stop` 后半截回复不消失。
- 切到别的会话后原会话完成出现未读点与标题计数，打开后清除。
- 侧栏改名后刷新页面仍是新标题。
- 后端测试与前端构建通过。
```

- [ ] **Step 2: 提交**

```bash
git add docs/intent.md
git commit -m "Record chat experience polish requirements

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: TurnStream 事件序号与 after 回放

**Files:**
- Modify: `apps/api/app/services/turn_stream.py`
- Test: `apps/api/app/tests/test_turn_stream.py`

- [ ] **Step 1: 写失败测试**

把 `apps/api/app/tests/test_turn_stream.py` 全文替换为（旧断言改为 `(index, event)` 元组，并新增 3 个用例）：

```python
import asyncio

import pytest

from app.services.turn_stream import TurnStream


@pytest.mark.asyncio
async def test_subscriber_receives_buffered_and_live_events():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )
    stream.publish({"type": "assistant_delta", "delta": "b"})
    assert await asyncio.wait_for(queue.get(), 1) == (
        1,
        {"type": "assistant_delta", "delta": "b"},
    )
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (2, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_publish_after_close_is_ignored():
    stream = TurnStream()
    stream.close()
    stream.publish({"type": "assistant_delta", "delta": "x"})
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (0, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_close_is_idempotent_and_delivers_turn_end_once_per_subscriber():
    stream = TurnStream()
    first = await stream.subscribe()
    second = await stream.subscribe()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    stream.close()
    stream.close()
    for queue in (first, second):
        assert await asyncio.wait_for(queue.get(), 1) == (
            0,
            {"type": "assistant_delta", "delta": "a"},
        )
        assert await asyncio.wait_for(queue.get(), 1) == (1, {"type": "turn_end"})
        assert queue.empty()


@pytest.mark.asyncio
async def test_close_does_not_duplicate_published_turn_end():
    stream = TurnStream()
    queue = await stream.subscribe()
    rich_end = {"type": "turn_end", "status": "completed"}
    stream.publish(rich_end)
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (0, rich_end)
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscriber_after_close_receives_only_published_turn_end():
    stream = TurnStream()
    rich_end = {"type": "turn_end", "status": "completed"}
    stream.publish({"type": "assistant_delta", "delta": "a"})
    stream.publish(rich_end)
    stream.close()
    queue = await stream.subscribe()
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )
    assert await asyncio.wait_for(queue.get(), 1) == (1, rich_end)
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_after_skips_already_seen_events():
    stream = TurnStream()
    for delta in ("a", "b", "c"):
        stream.publish({"type": "assistant_delta", "delta": delta})
    stream.publish({"type": "turn_end", "status": "completed"})
    queue = await stream.subscribe(after=2)
    assert await asyncio.wait_for(queue.get(), 1) == (
        2,
        {"type": "assistant_delta", "delta": "c"},
    )
    assert await asyncio.wait_for(queue.get(), 1) == (
        3,
        {"type": "turn_end", "status": "completed"},
    )


@pytest.mark.asyncio
async def test_subscribe_after_beyond_buffer_waits_for_new_events():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe(after=5)
    assert queue.empty()
    stream.publish({"type": "assistant_delta", "delta": "b"})
    assert await asyncio.wait_for(queue.get(), 1) == (
        1,
        {"type": "assistant_delta", "delta": "b"},
    )
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (2, {"type": "turn_end"})


@pytest.mark.asyncio
async def test_negative_after_replays_from_the_start():
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "a"})
    queue = await stream.subscribe(after=-3)
    assert await asyncio.wait_for(queue.get(), 1) == (
        0,
        {"type": "assistant_delta", "delta": "a"},
    )


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery_and_is_idempotent():
    stream = TurnStream()
    queue = await stream.subscribe()
    stream.unsubscribe(queue)
    stream.unsubscribe(queue)
    assert stream._subscribers == set()
    stream.publish({"type": "assistant_delta", "delta": "late"})
    assert queue.empty()


@pytest.mark.asyncio
async def test_unsubscribe_after_close_is_safe():
    stream = TurnStream()
    queue = await stream.subscribe()
    stream.close()
    assert await asyncio.wait_for(queue.get(), 1) == (0, {"type": "turn_end"})
    stream.unsubscribe(queue)
    assert stream._subscribers == set()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_turn_stream.py -q
```

Expected: FAIL，`TypeError`/断言失败（当前返回 `dict`，且 `subscribe()` 不接受 `after`）。

- [ ] **Step 3: 实现事件序号**

用下面内容替换 `apps/api/app/services/turn_stream.py`：

```python
"""In-memory event stream for one turn.

One stream exists per turn. It is live-only: nothing is persisted, so a page
refresh loses the live view by design. The terminal event is
``{"type": "turn_end"}``, delivered exactly once to each current and future
subscriber. The runner may publish the terminal event itself with extra
details; ``close()`` then only closes the stream instead of appending a bare
duplicate. Events published before a subscriber attaches are buffered and
replayed on subscribe. Each event carries a buffer index so a client that lost
its connection can resume after the last event it saw. ``close()`` is
idempotent and ignores later publishes.
"""

from __future__ import annotations

import asyncio
from typing import Any

Event = tuple[int, dict[str, Any]]


class TurnStream:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the stream has been closed and will accept no more events."""
        return self._closed

    def _has_end(self) -> bool:
        return bool(self._buffer) and self._buffer[-1].get("type") == "turn_end"

    def publish(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        index = len(self._buffer)
        self._buffer.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait((index, event))

    async def subscribe(self, after: int = 0) -> asyncio.Queue[Event]:
        """Replay buffered events from ``after`` and receive new ones."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        start = max(0, min(after, len(self._buffer)))
        for index in range(start, len(self._buffer)):
            queue.put_nowait((index, self._buffer[index]))
        if self._closed:
            if not self._has_end():
                queue.put_nowait((len(self._buffer), {"type": "turn_end"}))
            return queue
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Detach a subscriber. Idempotent and safe after ``close()``."""
        self._subscribers.discard(queue)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._has_end():
            index = len(self._buffer)
            for queue in list(self._subscribers):
                queue.put_nowait((index, {"type": "turn_end"}))
        self._subscribers.clear()
```

- [ ] **Step 3b: 更新事件消费方（兼容迁移）**

`subscribe()` 改为返回 `(index, event)` 后，以下消费者必须同时解包：

1. `apps/api/app/routers/sessions.py` 的流生成器：`_, event = await asyncio.wait_for(queue.get(), timeout=15)`（本任务先保持 SSE 输出形状不变，Task 3 再加 `id:` 与 `after`）。
2. `apps/api/app/tests/test_claude_runner.py::drain_until_end`：`_, event = await ...`。
3. `apps/api/app/tests/test_session_registry.py::drain_terminal`：`_, event = await ...`。
4. `apps/api/app/tests/test_claude_runner_delivery.py` 的内联 drain 循环：`_, event = await ...`。

运行 `.venv/bin/python -m pytest apps/api/app/tests -q`，期望 806 passed, 3 skipped（turn-stream 用例从 7 个增加到 10 个）。

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_turn_stream.py -q
```

Expected: PASS，全部用例通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/turn_stream.py apps/api/app/tests/test_turn_stream.py \
  apps/api/app/routers/sessions.py apps/api/app/tests/test_claude_runner.py \
  apps/api/app/tests/test_session_registry.py apps/api/app/tests/test_claude_runner_delivery.py
git commit -m "Number turn stream events for gap-free resume

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

> 拆成「实现提交 + 兼容提交」也可以，最终树必须全绿。

---

## Task 3: 流路由输出事件 id 并支持 after

**Files:**
- Modify: `apps/api/app/routers/sessions.py`
- Test: `apps/api/app/tests/test_chat_api.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_chat_api.py` 中替换 `test_stream_replays_events_and_unsubscribes`，并新增 `test_stream_after_skips_seen_events`：

```python
def test_stream_replays_events_and_unsubscribes(client):
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "hello"})
    stream.publish({"type": "turn_end", "status": "completed"})

    class StreamRegistry:
        def current_turn_id(self, session_id):
            return "turn-1"

        def stream(self, turn_id, session_id=None):
            if turn_id == "turn-1" and session_id == SESSION_ID:
                return stream
            return None

    client.app.state.registry = StreamRegistry()
    with client.stream("GET", f"/api/sessions/{SESSION_ID}/stream") as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "id: 0" in body
    assert "id: 1" in body
    assert "assistant_delta" in body
    assert "turn_end" in body
    assert stream._subscribers == set()


def test_stream_after_skips_seen_events(client):
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "old"})
    stream.publish({"type": "assistant_delta", "delta": "new"})
    stream.publish({"type": "turn_end", "status": "completed"})

    class StreamRegistry:
        def current_turn_id(self, session_id):
            return "turn-1"

        def stream(self, turn_id, session_id=None):
            return stream if turn_id == "turn-1" else None

    client.app.state.registry = StreamRegistry()
    with client.stream(
        "GET", f"/api/sessions/{SESSION_ID}/stream?turn_id=turn-1&after=1"
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert '"old"' not in body
    assert "id: 1" in body
    assert '"new"' in body
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k stream -q
```

Expected: FAIL，响应里没有 `id:` 行或 `after` 未生效。

- [ ] **Step 3: 修改路由**

在 `apps/api/app/routers/sessions.py` 中把 `stream_session` 替换为：

```python
@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    turn_id: str | None = None,
    after: int = 0,
) -> StreamingResponse:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    resolved = turn_id or registry.current_turn_id(session_id)
    stream = registry.stream(resolved, session_id) if resolved else None
    if stream is None:
        raise HTTPException(status_code=409, detail="no active turn for session")

    async def generator():
        queue = await stream.subscribe(after)
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    index, event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                yield f"id: {index}\ndata: {payload}\n\n"
                if event.get("type") == "turn_end":
                    return
        finally:
            stream.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k stream -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/routers/sessions.py apps/api/app/tests/test_chat_api.py
git commit -m "Stream turn events with ids and after offset

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: 轮次探测接口

**Files:**
- Modify: `apps/api/app/routers/sessions.py`
- Test: `apps/api/app/tests/test_chat_api.py`

- [ ] **Step 1: 写失败测试**

在 `RecordingRegistry.__init__` 中增加 `self.current_turn = None` 与 `self.turn_streams: dict[tuple[str, str], TurnStream] = {}`，并加方法：

```python
    def current_turn_id(self, session_id):
        return self.current_turn

    def stream(self, turn_id, session_id=None):
        return self.turn_streams.get((turn_id, session_id))
```

在 `test_chat_api.py` 末尾追加：

```python
def test_session_turn_without_registry_is_503(client):
    client.app.state.registry = None
    response = client.get(f"/api/sessions/{SESSION_ID}/turn")
    assert response.status_code == 503


def test_session_turn_reports_idle_session(client, fake_registry):
    fake_registry.busy = False
    fake_registry.current_turn = None
    response = client.get(f"/api/sessions/{SESSION_ID}/turn")
    assert response.status_code == 200
    assert response.json() == {"turn_id": None, "busy": False, "alive": False}


def test_session_turn_reports_running_and_queued_sessions(client, fake_registry):
    fake_registry.busy = True
    fake_registry.current_turn = "turn-9"
    response = client.get(f"/api/sessions/{SESSION_ID}/turn")
    assert response.json() == {"turn_id": "turn-9", "busy": True, "alive": False}

    fake_registry.current_turn = None
    response = client.get(f"/api/sessions/{SESSION_ID}/turn")
    assert response.json() == {"turn_id": None, "busy": True, "alive": False}


def test_session_turn_reports_whether_a_turn_is_alive(client, fake_registry):
    stream = TurnStream()
    fake_registry.turn_streams[("turn-9", SESSION_ID)] = stream
    response = client.get(f"/api/sessions/{SESSION_ID}/turn?turn_id=turn-9")
    assert response.json() == {"turn_id": None, "busy": False, "alive": True}

    stream.close()
    response = client.get(f"/api/sessions/{SESSION_ID}/turn?turn_id=turn-9")
    assert response.json()["alive"] is False

    response = client.get(f"/api/sessions/{SESSION_ID}/turn?turn_id=missing")
    assert response.json()["alive"] is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k session_turn -q
```

Expected: FAIL，404（路由不存在）。

- [ ] **Step 3: 增加路由**

在 `apps/api/app/routers/sessions.py` 的 `stream_session` 之后加入：

```python
@router.get("/sessions/{session_id}/turn")
async def session_turn(
    session_id: str, request: Request, turn_id: str | None = None
) -> dict[str, object]:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    probe = registry.stream(turn_id, session_id) if turn_id else None
    return {
        "turn_id": registry.current_turn_id(session_id),
        "busy": registry.is_busy(session_id),
        "alive": bool(probe is not None and not probe.closed),
    }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k session_turn -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/routers/sessions.py apps/api/app/tests/test_chat_api.py
git commit -m "Expose current turn status for stream reconnects

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: 按 turn 取消排队项

**Files:**
- Modify: `apps/api/app/services/session_registry.py`
- Test: `apps/api/app/tests/test_session_registry.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_session_registry.py` 末尾追加（复用现有 `FakeRunner`、`turn()`、`wait_until`、`drain_terminal`）：

```python
@pytest.mark.asyncio
async def test_cancel_queued_removes_only_that_turn_and_preserves_order():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    first_id = await registry.submit(turn(prompt="a"))
    second_id = await registry.submit(turn(prompt="b"))
    third_id = await registry.submit(turn(prompt="c"))
    await wait_until(lambda: runner.started == ["a"])

    second_stream = registry.stream(second_id)
    assert await registry.cancel_queued("s1", second_id) is True
    assert second_stream.closed
    assert await drain_terminal(second_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.started == ["a", "c"])
    runner.release("c")
    await wait_until(lambda: runner.finished == ["a", "c"])
    assert "b" not in runner.started
    assert registry.stream(first_id) is not None
    assert registry.stream(third_id) is not None
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_ignores_a_running_turn():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    running_id = await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.cancel_queued("s1", running_id) is False
    assert not registry.stream(running_id).closed
    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_cancels_turn_waiting_for_concurrency_permit():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=1)
    await registry.submit(turn(session_id="s1", prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    waiting_id = await registry.submit(turn(session_id="s2", prompt="b"))
    await wait_until(
        lambda: "s2" in registry._pending and "s2" not in registry._started
    )
    waiting_stream = registry.stream(waiting_id)

    assert await registry.cancel_queued("s2", waiting_id) is True
    assert waiting_stream.closed
    assert await drain_terminal(waiting_stream) == [
        {"type": "turn_end", "status": "cancelled"}
    ]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await wait_until(lambda: "s2" not in registry._workers)
    assert "b" not in runner.started
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_returns_false_for_unknown_turn():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.cancel_queued("s1", "missing") is False
    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


@pytest.mark.asyncio
async def test_cancel_queued_discards_a_queued_delivery():
    await init_db()
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id="s1"
        )
        await db.commit()
    queued = Turn(
        session_id="s1",
        agent_id="default",
        prompt="b",
        delivery=DeliveryRequest("ch1", "wx", delivery_id),
    )
    queued_id = await registry.submit(queued)
    try:
        assert await registry.cancel_queued("s1", queued_id) is True
        assert await channel_delivery.get(delivery_id) is None
    finally:
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
        await registry.close()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_registry.py -k cancel_queued -q
```

Expected: FAIL，`AttributeError: 'SessionRegistry' object has no attribute 'cancel_queued'`。

- [ ] **Step 3: 实现 `cancel_queued`**

在 `apps/api/app/services/session_registry.py` 的 `interrupt()` 之后加入：

```python
    async def cancel_queued(self, session_id: str, turn_id: str) -> bool:
        """Cancel one queued turn, leaving the running turn and the rest intact.

        Returns False when the turn has already started or is unknown. The
        drain and re-put below must not await: a submit can only land between
        awaits, and the worker relies on the queue staying consistent.
        """
        dropped: tuple[Turn, TurnStream] | None = None
        pending = self._pending.get(session_id)
        if (
            pending is not None
            and pending[0].id == turn_id
            and session_id not in self._started
        ):
            dropped = pending
            self._cancelled.add(turn_id)
        else:
            queue = self._queues.get(session_id)
            if queue is not None:
                kept: list[tuple[Turn, TurnStream]] = []
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    queue.task_done()
                    if dropped is None and item[0].id == turn_id:
                        dropped = item
                    else:
                        kept.append(item)
                for item in kept:
                    queue.put_nowait(item)
        if dropped is None:
            return False

        turn, stream = dropped
        stream.publish({"type": "turn_end", "status": "cancelled"})
        stream.close()
        if session_id not in self._running and session_id not in self._finishing:
            queue = self._queues.get(session_id)
            if queue is None or queue.empty():
                worker = self._workers.pop(session_id, None)
                if worker is not None:
                    worker.cancel()
                self._queues.pop(session_id, None)
                self._current.pop(session_id, None)
        await _discard_delivery(turn)
        return True
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_session_registry.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/session_registry.py apps/api/app/tests/test_session_registry.py
git commit -m "Cancel one queued turn without stopping the session

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: 取消排队路由

**Files:**
- Modify: `apps/api/app/routers/sessions.py`
- Test: `apps/api/app/tests/test_chat_api.py`

- [ ] **Step 1: 写失败测试**

给 `RecordingRegistry` 增加：

```python
        self.cancel_calls = []
        self.cancel_result = True
```

和方法：

```python
    async def cancel_queued(self, session_id, turn_id):
        self.cancel_calls.append((session_id, turn_id))
        return self.cancel_result
```

在 `test_chat_api.py` 末尾追加：

```python
def test_cancel_queued_turn_returns_result(client, fake_registry):
    response = client.post(
        f"/api/sessions/{SESSION_ID}/turns/turn-2/cancel"
    )
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert fake_registry.cancel_calls == [(SESSION_ID, "turn-2")]

    fake_registry.cancel_result = False
    response = client.post(
        f"/api/sessions/{SESSION_ID}/turns/turn-2/cancel"
    )
    assert response.json() == {"cancelled": False}


def test_cancel_queued_turn_without_registry_is_503(client):
    client.app.state.registry = None
    response = client.post(
        f"/api/sessions/{SESSION_ID}/turns/turn-2/cancel"
    )
    assert response.status_code == 503
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k cancel_queued -q
```

Expected: FAIL，404（路由不存在）。

- [ ] **Step 3: 增加路由**

在 `apps/api/app/routers/sessions.py` 的 `session_turn` 之后加入：

```python
@router.post("/sessions/{session_id}/turns/{turn_id}/cancel")
async def cancel_queued_turn(
    session_id: str, turn_id: str, request: Request
) -> dict[str, bool]:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    cancelled = await registry.cancel_queued(session_id, turn_id)
    return {"cancelled": bool(cancelled)}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_chat_api.py -k cancel_queued -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/routers/sessions.py apps/api/app/tests/test_chat_api.py
git commit -m "Add queued turn cancellation endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: 前端 API 与冻结类型

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/types.ts`

- [ ] **Step 1: 在 `types.ts` 增加 `FrozenLive`**

放在 `LiveTask` 定义之后：

```ts
export type FrozenLive = {
  streamText: string;
  streamReasoning: string;
  activity: LiveToolCall[];
  subagents: Record<string, LiveSubagent>;
  tasks: Record<string, LiveTask>;
  artifacts: MessageArtifact[];
};
```

- [ ] **Step 2: 在 `api.ts` 增加方法**

在 `interruptSession` 附近加入：

```ts
  cancelQueuedTurn: (sessionId: string, turnId: string) =>
    request<{ cancelled: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      { method: "POST" },
    ),
  getSessionTurn: (sessionId: string, turnId?: string) => {
    const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : "";
    return request<{ turn_id: string | null; busy: boolean; alive: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/turn${query}`,
    );
  },
  renameSession: (sessionId: string, agentId: string, title: string) =>
    request<{ renamed: boolean }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/rename?agent_id=${encodeURIComponent(agentId)}`,
      { method: "POST", body: JSON.stringify({ title }) },
    ),
```

把 `sessionStreamUrl` 替换为：

```ts
  sessionStreamUrl: (sessionId: string, turnId?: string, after?: number) => {
    const params = new URLSearchParams();
    if (turnId) params.set("turn_id", turnId);
    if (after && after > 0) params.set("after", String(after));
    const query = params.toString();
    return `/api/sessions/${encodeURIComponent(sessionId)}/stream${
      query ? `?${query}` : ""
    }`;
  },
```

- [ ] **Step 3: 构建确认类型通过**

```bash
cd apps/web && npm run build
```

Expected: `✓ built in …`，没有 TypeScript 错误。

- [ ] **Step 4: 提交**

```bash
git add apps/web/src/api.ts apps/web/src/types.ts
git commit -m "Add chat polish API client and frozen live type

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8: 排队序号与单条撤回（前端）

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/components/MessageList.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: App 增加取消状态与队列元数据**

在 `const [focus, setFocus] = useState<FocusTarget | null>(null);` 之后加入：

```ts
  const [cancellingQueued, setCancellingQueued] = useState<Record<string, true>>({});
```

在 `queuedMessages` 之后加入：

```ts
  const queuedInfo = useMemo(() => {
    const items = (pendingUserBySession[sessionId] ?? []).filter(
      (item) => item.queued,
    );
    const info: Record<
      string,
      { index: number; total: number; canCancel: boolean }
    > = {};
    items.forEach((item, index) => {
      info[item.id] = {
        index: index + 1,
        total: items.length,
        canCancel: Boolean(item.turnId),
      };
    });
    return info;
  }, [pendingUserBySession, sessionId]);
```

在 `QUEUED_NOTICE` 常量之后加辅助函数：

```ts
function queuedNotice(count: number): string {
  return count > 0
    ? `已排队 ${count} 条，前面的任务完成后依次执行`
    : QUEUED_NOTICE;
}
```

- [ ] **Step 2: App 增加 `cancelQueued`**

在 `clearQueuedTurns` 之后加入：

```ts
  async function cancelQueued(sessionId: string, messageId: string) {
    const item = (pendingUsersRef.current[sessionId] ?? []).find(
      (entry) => entry.id === messageId,
    );
    if (!item?.turnId) return;
    const turnId = item.turnId;
    setCancellingQueued((current) => ({ ...current, [messageId]: true }));
    try {
      const result = await api.cancelQueuedTurn(sessionId, turnId);
      if (result.cancelled) {
        removePendingUser(sessionId, messageId);
        const key = sessionQueueKey(sessionId);
        const remaining = (queuedTurnsRef.current[key] ?? []).filter(
          (id) => id !== turnId,
        );
        if (remaining.length > 0) {
          queuedTurnsRef.current = {
            ...queuedTurnsRef.current,
            [key]: remaining,
          };
        } else {
          clearQueuedTurns(sessionId);
        }
        const live = pendingUsersRef.current[sessionId] ?? [];
        const waiting = live.filter((entry) => entry.queued).length;
        updateLive(sessionId, (current) => ({
          ...current,
          notice: waiting > 0 ? queuedNotice(waiting) : "",
        }));
      } else {
        await refreshTranscript(sessionId);
        updateLive(sessionId, (current) => ({
          ...current,
          notice: "这条已经开始或已经结束，队列已刷新",
        }));
      }
    } catch (exc) {
      setSessionError(describeApiError(exc), sessionId);
    } finally {
      setCancellingQueued((current) => {
        const next = { ...current };
        delete next[messageId];
        return next;
      });
    }
  }
```

> `refreshTranscript` 定义在下方；函数声明提升允许这样调用。`setSessionError` 已存在。

- [ ] **Step 3: App 传入稳定回调和排队元数据**

在组件内 ref 区域（`const restoreSessionRef = ...` 之后）加入：

```ts
  const cancelQueuedRef = useRef(cancelQueued);
  cancelQueuedRef.current = cancelQueued;
  const cancelQueuedMessage = useCallback((messageId: string) => {
    void cancelQueuedRef.current(sessionIdRef.current, messageId);
  }, []);
```

把 `import { lazy, Suspense, useEffect, useMemo, useRef, useState }` 改为：

```ts
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
```

在 `send()` 里把排队 notice 改为数量：找到

```ts
        updateLive(targetSessionId, (current) => ({ ...current, notice: QUEUED_NOTICE }));
```

替换为：

```ts
        const waiting = (pendingUsersRef.current[targetSessionId] ?? []).filter(
          (item) => item.queued,
        ).length;
        updateLive(targetSessionId, (current) => ({
          ...current,
          notice: queuedNotice(waiting),
        }));
```

在 `ChatPanel` 的属性中、`queuedMessages={queuedMessages}` 之后加入：

```tsx
              queuedInfo={queuedInfo}
              cancellingQueued={cancellingQueued}
              onCancelQueued={cancelQueuedMessage}
```

- [ ] **Step 4: ChatPanel 透传**

在 `ChatPanelProps` 中加入：

```ts
  queuedInfo: Record<
    string,
    { index: number; total: number; canCancel: boolean }
  >;
  cancellingQueued: Record<string, true>;
  onCancelQueued: (messageId: string) => void;
```

在传给 `MessageList` 的属性中、`queuedMessages={props.queuedMessages}` 之后加入：

```tsx
            queuedInfo={props.queuedInfo}
            cancellingQueued={props.cancellingQueued}
            onCancelQueued={props.onCancelQueued}
```

- [ ] **Step 5: MessageList 渲染排队标签与撤回按钮**

在 `MessageListProps` 中加入：

```ts
  queuedInfo: Record<
    string,
    { index: number; total: number; canCancel: boolean }
  >;
  cancellingQueued: Record<string, true>;
  onCancelQueued: (messageId: string) => void;
```

把 `UserMessageRow` 的签名改为：

```tsx
const UserMessageRow = memo(function UserMessageRow({
  message,
  agentId,
  highlighted,
  onQuote,
  queueIndex = 0,
  queueTotal = 0,
  queueCanCancel = false,
  queueCancelling = false,
  onCancelQueued,
}: {
  message: TranscriptMessage;
  agentId: string;
  highlighted: boolean;
  onQuote: (text: string) => void;
  queueIndex?: number;
  queueTotal?: number;
  queueCanCancel?: boolean;
  queueCancelling?: boolean;
  onCancelQueued?: (messageId: string) => void;
}) {
```

在 `return (` 的 `message-row user` 内、`message-bubble user` 之前插入：

```tsx
      {queueTotal > 0 && (
        <div className="message-queue-state">
          <span className="message-queue-label">
            {queueCanCancel ? `排队中 ${queueIndex}/${queueTotal}` : "正在提交…"}
          </span>
          <button
            type="button"
            className="message-queue-cancel"
            disabled={!queueCanCancel || queueCancelling}
            title="撤回这条排队消息"
            aria-label="撤回这条排队消息"
            onClick={() => onCancelQueued?.(message.id)}
          >
            ×
          </button>
        </div>
      )}
```

在渲染 `props.queuedMessages` 的 `UserMessageRow` 处传入：

```tsx
          queueIndex={props.queuedInfo[message.id]?.index ?? 0}
          queueTotal={props.queuedInfo[message.id]?.total ?? 0}
          queueCanCancel={props.queuedInfo[message.id]?.canCancel ?? false}
          queueCancelling={Boolean(props.cancellingQueued[message.id])}
          onCancelQueued={props.onCancelQueued}
```

- [ ] **Step 6: 增加样式**

在 `.message-footer.user` 规则之后加入：

```css
.message-queue-state {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 4px;
  color: var(--muted);
  font-size: 12px;
}

.message-queue-cancel {
  border: 0;
  border-radius: 6px;
  padding: 2px 5px;
  background: transparent;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
}

.message-queue-cancel:hover:not(:disabled) {
  color: var(--text);
  background: rgba(var(--ink-rgb) / 0.06);
}

.message-queue-cancel:disabled {
  opacity: 0.45;
  cursor: default;
}
```

- [ ] **Step 7: 构建并手工验证**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。手工检查：运行中发三条追问，看到「排队中 1/3」等标签；点第二条的 × 后该气泡消失，其余序号更新；第一条执行完第三条接着执行。

- [ ] **Step 8: 提交**

```bash
git add apps/web/src/App.tsx apps/web/src/components/ChatPanel.tsx apps/web/src/components/MessageList.tsx apps/web/src/styles.css
git commit -m "Show queued follow-up order and allow single cancellation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 9: SSE 断线续传（前端）

**Files:**
- Modify: `apps/web/src/App.tsx`

- [ ] **Step 1: 增加常量与 refs**

在 `QUEUED_NOTICE` 常量旁加入：

```ts
const RECONNECT_NOTICE = "连接中断，正在重连…";
const RECONNECT_LOST_NOTICE = "连接已断开，已刷新为最新历史";
```

在 `restoreSessionRef` 之后加入：

```ts
  const lastEventIndexRef = useRef<Record<string, number>>({});
  const reconnectAttemptsRef = useRef<Record<string, number>>({});
```

- [ ] **Step 2: 更新 `bindStreamSource`**

替换为：

```ts
  function bindStreamSource(forSession: string, source: EventSource) {
    source.onmessage = (message) => {
      let event: TurnStreamEvent;
      try {
        event = JSON.parse(message.data) as TurnStreamEvent;
      } catch {
        return;
      }
      if (message.lastEventId !== "") {
        const index = Number(message.lastEventId);
        if (Number.isFinite(index)) {
          lastEventIndexRef.current[forSession] = index;
          reconnectAttemptsRef.current[forSession] = 0;
        }
      }
      applyStreamEvent(forSession, source, event);
    };
    source.onopen = () => {
      updateLive(forSession, (current) =>
        current.notice === RECONNECT_NOTICE
          ? { ...current, notice: "" }
          : current,
      );
    };
  }
```

- [ ] **Step 3: 替换 `openStream` 的错误处理**

把 `openStream` 替换为：

```ts
  function openStream(forSession: string, turnId?: string, after = 0) {
    // One subscription per turn: re-subscribing replays the stream buffer and
    // would append the same deltas twice.
    if (streamsRef.current.has(forSession)) return;
    closeStream(forSession);
    if (!sessionAgentRef.current[forSession]) {
      sessionAgentRef.current[forSession] = agentIdRef.current;
    }
    lastEventIndexRef.current[forSession] = after > 0 ? after - 1 : -1;
    const source = new EventSource(
      api.sessionStreamUrl(forSession, turnId, after),
    );
    streamsRef.current.set(forSession, source);
    if (turnId) activeTurnRef.current[forSession] = turnId;
    else delete activeTurnRef.current[forSession];
    updateLive(forSession, (current) => ({ ...current, tracking: true }));
    bindStreamSource(forSession, source);

    source.onerror = () => {
      if (streamsRef.current.get(forSession) !== source) return;
      const reconnectTurnId = activeTurnRef.current[forSession] ?? null;
      const attachMode = reconnectTurnId === null;
      const attempt = (reconnectAttemptsRef.current[forSession] ?? 0) + 1;
      reconnectAttemptsRef.current[forSession] = attempt;
      closeStream(forSession);
      void reconnectStream(forSession, reconnectTurnId, attachMode, attempt);
    };
  }
```

- [ ] **Step 4: 增加续传状态机**

在 `openStream` 之后加入：

```ts
  async function reconnectStream(
    forSession: string,
    turnId: string | null,
    attachMode: boolean,
    attempt: number,
  ) {
    if (streamsRef.current.has(forSession)) return;
    let status: {
      turn_id: string | null;
      busy: boolean;
      alive: boolean;
    } | null = null;
    try {
      status = await api.getSessionTurn(forSession, turnId ?? undefined);
    } catch {
      status = null;
    }
    if (status && attachMode) {
      if (status.busy) {
        if (attempt > 8) {
          abandonStream(forSession);
          return;
        }
        scheduleStreamAttach(forSession, attempt);
      } else {
        reconnectAttemptsRef.current[forSession] = 0;
        updateLive(forSession, (current) => ({
          ...current,
          tracking: false,
          notice: "",
        }));
        void refreshTranscript(forSession);
      }
      return;
    }
    if (status && turnId && status.alive) {
      if (attempt > 8) {
        abandonStream(forSession);
        return;
      }
      scheduleStreamReconnect(forSession, turnId, attempt);
      return;
    }
    if (status && turnId) {
      // The turn ended (or its stream was evicted) while disconnected.
      reconnectAttemptsRef.current[forSession] = 0;
      updateLive(forSession, (current) => ({ ...current, tracking: false }));
      void refreshTranscript(forSession).then(() => {
        if (status?.busy && !streamsRef.current.has(forSession)) {
          scheduleStreamAttach(forSession, attempt);
        }
      });
      return;
    }
    if (attempt > 8) {
      abandonStream(forSession);
      return;
    }
    scheduleStreamProbe(forSession, turnId, attachMode, attempt + 1);
  }

  function scheduleStreamAttach(forSession: string, attempt: number) {
    updateLive(forSession, (current) => ({
      ...current,
      notice: RECONNECT_NOTICE,
    }));
    const delay = Math.min(500 * 2 ** Math.min(attempt, 4), 5000);
    const timer = window.setTimeout(() => {
      retryTimersRef.current.delete(forSession);
      if (streamsRef.current.has(forSession)) return;
      openStream(forSession);
    }, delay);
    retryTimersRef.current.set(forSession, timer);
  }

  function scheduleStreamReconnect(
    forSession: string,
    turnId: string,
    attempt: number,
  ) {
    updateLive(forSession, (current) => ({
      ...current,
      notice: RECONNECT_NOTICE,
    }));
    const delay = Math.min(500 * 2 ** Math.min(attempt, 4), 5000);
    const timer = window.setTimeout(() => {
      retryTimersRef.current.delete(forSession);
      if (streamsRef.current.has(forSession)) return;
      const after = (lastEventIndexRef.current[forSession] ?? -1) + 1;
      openStream(forSession, turnId, after);
    }, delay);
    retryTimersRef.current.set(forSession, timer);
  }

  function scheduleStreamProbe(
    forSession: string,
    turnId: string | null,
    attachMode: boolean,
    attempt: number,
  ) {
    updateLive(forSession, (current) => ({
      ...current,
      notice: RECONNECT_NOTICE,
    }));
    const delay = Math.min(500 * 2 ** Math.min(attempt, 4), 5000);
    const timer = window.setTimeout(() => {
      retryTimersRef.current.delete(forSession);
      if (streamsRef.current.has(forSession)) return;
      void reconnectStream(forSession, turnId, attachMode, attempt);
    }, delay);
    retryTimersRef.current.set(forSession, timer);
  }

  function abandonStream(forSession: string) {
    reconnectAttemptsRef.current[forSession] = 0;
    updateLive(forSession, (current) => ({
      ...current,
      tracking: false,
      notice: RECONNECT_LOST_NOTICE,
    }));
    void refreshTranscript(forSession);
  }
```

> `scheduleStreamReconnect` 在探测确认旧轮仍存活（`alive`）后按 `lastEventIndex + 1` 重连；`scheduleStreamProbe` 只用于探测请求失败后的退避重试；`scheduleStreamAttach` 在不知道 turn id 或旧轮结束后需要重新挂当前轮时使用，同样带退避。`alive` 回答的是「这个 turn 的流是否还能订阅」，而不是「它是不是当前轮」，因此排队中的 turn 不会被误判为已结束。
>
> 落地时的加固（提交 `a385787`）：`openStream` 在 `after === 0` 时先清空实时累加器（服务端会整段重放，避免 attach/轮询重连把旧文本叠一遍）；`onopen` 同时清除 `RECONNECT_NOTICE` 与 `RECONNECT_LOST_NOTICE`；三个 schedule 共用 `scheduleStreamRetry`，先清旧计时器并在回调里只删除自己的条目；`MAX_STREAM_RETRIES = 8`；`closeAllStreams` 递增 `streamGenerationRef` 并重置索引/次数，`reconnectStream` 在探测 await 之后校验 generation，防止登出后继续重连。

- [ ] **Step 5: 构建并手工验证**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。手工检查：长任务执行中在 DevTools 选择 Offline 3–5 秒再恢复，出现「连接中断，正在重连…」，恢复后文本不重复、不缺字。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/App.tsx
git commit -m "Resume chat streams from the last received event

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 10: 停止保留半截回复（前端）

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/components/MessageList.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: App 的 SessionLive 增加 frozen**

把 `SessionLive` 定义改为复用冻结类型，避免字段漂移：

```ts
type SessionLive = FrozenLive & {
  notice: string;
  tracking: boolean;
  frozen: FrozenLive | null;
};
```

在 import 中加入 `FrozenLive`：

```ts
import type {
  Agent,
  AgentSavePayload,
  Attachment,
  DrawerTab,
  FrozenLive,
  LiveSubagent,
  LiveTask,
  LiveToolCall,
  MessageArtifact,
  Provider,
  SessionSummary,
  SkillInfo,
  TranscriptMessage,
  TurnStreamEvent,
} from "./types";
```

在 `EMPTY_LIVE` 中加入：

```ts
  frozen: null,
```

- [ ] **Step 2: 增加历史回复判断函数**

在组件外（`transcriptMessage` 之后）加入：

```ts
/** Whether the transcript contains a non-empty assistant reply after the last user question. */
function transcriptHasReply(list: TranscriptMessage[]): boolean {
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const message = list[index];
    if (message.role === "user") return false;
    if (message.role === "assistant" && message.content.trim()) return true;
  }
  return false;
}

/** Whether the interrupted partial already landed in the transcript. */
function frozenReplyPersisted(
  list: TranscriptMessage[],
  frozen: FrozenLive,
): boolean {
  const text = frozen.streamText.trim();
  if (!text) return transcriptHasReply(list);
  const head = text.slice(0, 120);
  return list.some((message) => {
    if (message.role !== "assistant") return false;
    const content = message.content.trim();
    if (!content) return false;
    return (
      content.startsWith(head) ||
      (content.length >= 20 && head.startsWith(content))
    );
  });
}
```

- [ ] **Step 3: 修改 `cancel()` 冻结实时内容**

把 `cancel()` 中 `const result = await api.interruptSession(id);` 到 `delete activeTurnRef.current[id];` 之间的代码替换为：

```ts
      const result = await api.interruptSession(id);
      if (result.interrupted) {
        updateLive(id, (current) => ({
          ...current,
          tracking: false,
          notice: "已请求停止当前任务。",
          frozen: {
            streamText: current.streamText,
            streamReasoning: current.streamReasoning,
            activity: current.activity,
            subagents: current.subagents,
            tasks: current.tasks,
            artifacts: current.artifacts,
          },
          streamText: "",
          streamReasoning: "",
          activity: [],
          subagents: {},
          tasks: {},
          artifacts: [],
        }));
      }
      closeStream(id);
      delete activeTurnRef.current[id];
```

在幸存排队 turn 的分支里，把 `updateLive` 替换为：

```ts
        updateLive(id, (current) => ({
          ...EMPTY_LIVE,
          tracking: true,
          notice: "已请求停止当前任务。",
          frozen: current.frozen,
        }));
```

- [ ] **Step 4: `refreshTranscript` 收敛 frozen**

在 `refreshTranscript` 的 `let failure = "";` 后加入：

```ts
    let loaded: TranscriptMessage[] | null = null;
```

在 `setMessagesFor(forSession, list);` 之前加入：

```ts
      loaded = list;
```

在 `finally` 的 `updateLive(forSession, (current) => ({ ...current, ... }))` 中，把 `frozen` 字段加进去：

```ts
        frozen:
          current.frozen && loaded && frozenReplyPersisted(loaded, current.frozen)
            ? null
            : current.frozen,
```

> 用内容前缀匹配判断半截回复是否已落盘，而不是只看「最后一条 user 之后有没有 assistant」：停止时如果还有幸存的排队 turn，最后一条 user 已经变成新的追问，只看顺序会误判。

- [ ] **Step 5: 打开会话与发送时清空 frozen**

在 `openSession` 开头加入：

```ts
    setLiveBySession((current) => {
      const session = current[id];
      if (!session?.frozen) return current;
      return { ...current, [id]: { ...session, frozen: null } };
    });
```

在 `send()` 的 `if (!alreadyStreaming) { updateLive(targetSessionId, () => ({ ...EMPTY_LIVE, tracking: true })); }` 处，`EMPTY_LIVE` 已包含 `frozen: null`，无需再改。

- [ ] **Step 6: ChatPanel / MessageList 渲染 frozen**

在 `ChatPanelProps` 中加入：

```ts
  frozen: FrozenLive | null;
```

在 import 的 types 中加入 `FrozenLive`，并在传给 `MessageList` 的属性里加入：

```tsx
            frozen={props.frozen}
```

在 `MessageListProps` 中加入：

```ts
  frozen: FrozenLive | null;
```

在 import 中加入 `FrozenLive`。

把 `MessageList` 中 `showStreaming` 的渲染块保留在末尾，并在它之前插入 frozen 行：

```tsx
      {props.frozen && (
        <StreamingRow
          streamText={props.frozen.streamText}
          streamReasoning={props.frozen.streamReasoning}
          activity={props.frozen.activity}
          subagents={props.frozen.subagents}
          tasks={props.frozen.tasks}
          liveArtifacts={props.frozen.artifacts}
          agentId={props.agentId}
          agentName={props.agentName}
          stopped
        />
      )}

      {showStreaming && (
        <StreamingRow
          streamText={props.streamText}
          streamReasoning={props.streamReasoning}
          activity={props.activity}
          subagents={props.subagents}
          tasks={props.tasks}
          liveArtifacts={props.liveArtifacts}
          agentId={props.agentId}
          agentName={props.agentName}
        />
      )}
```

> frozen 行与新的流式行可以同时出现：停止后幸存排队 turn 开始执行时，上一轮的「已停止」行留在上方，新 turn 从干净的气泡开始。

- [ ] **Step 7: StreamingRow 支持 stopped**

在 `StreamingRow` 的参数和解构中加入 `stopped = false` 与类型 `stopped?: boolean`，然后：

- 把 `{latestActivity && (` 改为 `{!stopped && latestActivity && (`。
- 在 `message-author` 后加入：

```tsx
          {stopped && <span className="message-stopped">已停止</span>}
```

- 把文本回退改为：

```tsx
        {streamText ? (
          <Markdown content={streamText} streaming agentId={agentId} />
        ) : (
          <div className="muted">{stopped ? "已停止" : "正在生成…"}</div>
        )}
```

- 把 `<span className="cursor" />` 改为：

```tsx
        {!stopped && <span className="cursor" />}
```

- [ ] **Step 8: 在 App 传入 frozen**

在 `ChatPanel` 的 `liveArtifacts={live.artifacts}` 之后加入：

```tsx
              frozen={live.frozen}
```

- [ ] **Step 9: 增加停止标记样式**

在 `.message-author` 规则之后加入：

```css
.message-stopped {
  padding: 1px 8px;
  border-radius: 999px;
  background: rgba(var(--ink-rgb) / 0.08);
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.02em;
}
```

- [ ] **Step 10: 构建并手工验证**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。手工检查：长回复执行中点停止，气泡变成「已停止」且保留文本；历史里有落盘的半截回复时刷新后显示历史行，不重复。

- [ ] **Step 11: 提交**

```bash
git add apps/web/src/App.tsx apps/web/src/components/ChatPanel.tsx apps/web/src/components/MessageList.tsx apps/web/src/styles.css
git commit -m "Keep the interrupted reply visible after stop

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 11: 完成未读提醒（前端）

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/Sidebar.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: App 增加未读状态**

在 `cancellingQueued` 状态旁加入：

```ts
  const [unreadBySession, setUnreadBySession] = useState<
    Record<string, "done" | "failed">
  >({});
```

在组件内加入辅助函数（放在 `applySessions` 附近）：

```ts
  function markUnread(id: string, status: "done" | "failed") {
    setUnreadBySession((current) =>
      current[id] === status ? current : { ...current, [id]: status },
    );
  }

  function clearUnread(id: string) {
    setUnreadBySession((current) => {
      if (!(id in current)) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function shouldMarkUnread(id: string): boolean {
    return (
      id !== sessionIdRef.current ||
      document.visibilityState !== "visible" ||
      !document.hasFocus()
    );
  }
```

- [ ] **Step 2: `finishTurn` 记录未读**

在 `finishTurn` 里、`if (forSession === sessionIdRef.current) attachIfBusy(forSession);` 之前加入：

```ts
    if (
      (status === "completed" || status === "failed") &&
      shouldMarkUnread(forSession)
    ) {
      markUnread(forSession, status === "completed" ? "done" : "failed");
    }
```

- [ ] **Step 3: 清除规则与标题**

在 `openSession(id)` 开头加入：

```ts
    clearUnread(id);
```

在 effect 区域加入：

```ts
  useEffect(() => {
    if (!sessionId) return;
    if (document.visibilityState === "visible") clearUnread(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    const clearActive = () => {
      const id = sessionIdRef.current;
      if (id) clearUnread(id);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") clearActive();
    };
    window.addEventListener("focus", clearActive);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", clearActive);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const count = sessions.reduce(
      (total, session) => total + (unreadBySession[session.id] ? 1 : 0),
      0,
    );
    document.title =
      count > 0 ? `(${count}) Agent Command Center` : "Agent Command Center";
  }, [sessions, unreadBySession]);
```

- [ ] **Step 4: Sidebar 显示未读点**

在 `SidebarProps` 中加入：

```ts
  unread: Record<string, "done" | "failed">;
```

在 `session-title` 内、标题文本之前加入：

```tsx
                    {props.unread[session.id] && (
                      <span
                        className={`session-unread ${props.unread[session.id]}`}
                        role="img"
                        title={
                          props.unread[session.id] === "failed"
                            ? "执行失败"
                            : "已完成"
                        }
                        aria-label={
                          props.unread[session.id] === "failed"
                            ? "执行失败"
                            : "已完成"
                        }
                      />
                    )}
```

在 App 的 `<Sidebar>` 属性中加入：

```tsx
        unread={unreadBySession}
```

- [ ] **Step 5: 增加未读点样式**

在 `.session-title` 规则之后加入：

```css
.session-unread {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 6px;
  border-radius: 50%;
  vertical-align: 1px;
}

.session-unread.done {
  background: var(--ok);
}

.session-unread.failed {
  background: var(--danger);
}
```

- [ ] **Step 6: 构建并手工验证**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。手工检查：在会话 B 看会话 A 执行完，A 出现未读点、标题变 `(1) …`；点开 A 后消失；当前会话切到别的标签页后完成，切回浏览器清除。

- [ ] **Step 7: 提交**

```bash
git add apps/web/src/App.tsx apps/web/src/components/Sidebar.tsx apps/web/src/styles.css
git commit -m "Mark completed sessions unread when the user is away

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 12: 侧栏会话改名（前端）

**Files:**
- Create: `apps/web/src/components/RenameSessionDialog.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/Sidebar.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 新建弹窗组件**

创建 `apps/web/src/components/RenameSessionDialog.tsx`：

```tsx
import { FormEvent, useEffect, useState } from "react";

type RenameSessionDialogProps = {
  open: boolean;
  title: string;
  onClose: () => void;
  onRename: (title: string) => Promise<void>;
};

export function RenameSessionDialog({
  open,
  title,
  onClose,
  onRename,
}: RenameSessionDialogProps) {
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setValue(title);
    setBusy(false);
    setError("");
  }, [open, title]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const trimmed = value.trim();
  const unchanged = trimmed === title.trim();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || unchanged || busy) return;
    setBusy(true);
    setError("");
    try {
      await onRename(trimmed);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal"
        onClick={(event) => event.stopPropagation()}
        onSubmit={submit}
      >
        <div className="modal-heading">
          <h2>重命名会话</h2>
          <button type="button" className="icon-button" onClick={onClose}>
            ×
          </button>
        </div>
        <label className="field-label" htmlFor="rename-session-title">
          标题
        </label>
        <input
          id="rename-session-title"
          autoFocus
          maxLength={80}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onFocus={(event) => event.target.select()}
        />
        {error && <div className="error-banner">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose}>
            取消
          </button>
          <button
            className="primary"
            type="submit"
            disabled={busy || !trimmed || unchanged}
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 2: Sidebar 增加重命名按钮**

在 `SidebarProps` 中加入：

```ts
  onRenameSession: (id: string) => void;
```

在删除按钮之前加入：

```tsx
                <button
                  className="icon-button subtle session-rename"
                  title="重命名会话"
                  aria-label="重命名会话"
                  onClick={(event) => {
                    event.stopPropagation();
                    props.onRenameSession(session.id);
                  }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                  </svg>
                </button>
```

- [ ] **Step 3: App 接入弹窗**

在 import 区加入：

```ts
import { RenameSessionDialog } from "./components/RenameSessionDialog";
```

在 `sessionToDelete` 状态旁加入：

```ts
  const [sessionToRename, setSessionToRename] = useState<SessionSummary | null>(
    null,
  );
```

在组件内加入：

```ts
  async function renameSession(sessionId: string, title: string) {
    const owner = sessionAgentRef.current[sessionId] ?? agentId;
    await api.renameSession(sessionId, owner, title);
    applySessions(
      sessionsRef.current.map((item) =>
        item.id === sessionId ? { ...item, title } : item,
      ),
    );
    setSessionToRename(null);
    void refreshSessions();
  }
```

在 `<Sidebar>` 属性中加入：

```tsx
        onRenameSession={(id) => {
          setSessionToRename(sessions.find((item) => item.id === id) ?? null);
        }}
```

在 `<ConfirmDialog>` 之后加入：

```tsx
      <RenameSessionDialog
        open={sessionToRename !== null}
        title={sessionToRename?.title || "新会话"}
        onClose={() => setSessionToRename(null)}
        onRename={(title) =>
          sessionToRename
            ? renameSession(sessionToRename.id, title)
            : Promise.resolve()
        }
      />
```

- [ ] **Step 4: 增加按钮样式**

在 `.session-unread` 规则之后加入：

```css
.session-rename {
  opacity: 0;
  pointer-events: none;
  transition: opacity 120ms ease;
}

.session-item:hover .session-rename,
.session-item:focus-within .session-rename,
.session-item.selected .session-rename {
  opacity: 1;
  pointer-events: auto;
}

@media (hover: none) {
  .session-rename {
    opacity: 1;
    pointer-events: auto;
  }
}
```

- [ ] **Step 5: 构建并手工验证**

```bash
cd apps/web && npm run build
```

Expected: 构建成功。手工检查：悬停会话行出现铅笔；改名保存后侧栏与聊天顶栏立即更新；刷新页面仍是新标题；空标题或未修改时保存按钮禁用。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/App.tsx apps/web/src/components/Sidebar.tsx apps/web/src/components/RenameSessionDialog.tsx apps/web/src/styles.css
git commit -m "Rename sessions from the sidebar

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 13: 文档同步与整体验收

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 `CLAUDE.md` 的网页聊天段落**

找到「网页聊天：`POST /api/chat/messages` …」这一条，把其中：

```text
同进程刷新会在数秒内通过 `current_turn_id` 重放当前轮缓冲；进程重启或断线才丢失，历史从 transcript 重建。
```

替换为：

```text
同进程刷新会在数秒内通过 `current_turn_id` 重放当前轮缓冲；SSE 断线按事件序号自动续传，进程重启才丢失，历史从 transcript 重建。
```

并在这一条末尾补充：

```text
运行中排队的追问按提交顺序显示「排队中 N/M」，可通过 `POST /api/sessions/{uuid}/turns/{turn_id}/cancel` 单独撤回；`/stop` 仍一次取消当前与全部排队。SSE 事件带递增 id，`GET /api/sessions/{uuid}/stream?turn_id=...&after=N` 从第 N 条之后续传；客户端断线先查 `GET /api/sessions/{uuid}/turn` 判断旧轮是否存活，再按序号重连。停止时前端冻结当前实时内容，历史已落盘则收敛为历史行，否则显示「已停止」行。切走会话或页面不可见时轮次完成/失败会在侧栏记未读点并在标签标题显示计数（仅内存）。侧栏会话行提供重命名弹窗，调用现有 rename 接口。
```

- [ ] **Step 2: 更新 `README.md` 的 `## Chat` 段落**

把这一条：

```markdown
- 刷新或断线后当前流式输出可能丢失：同一进程内前端会在数秒内通过 `current_turn_id` 重新订阅并重放当前轮缓冲；进程重启或 SSE 断开且错过缓冲时只能从 transcript 读回历史，执行过程从 thinking / tool_use 块尽量重建，信息少于实时流。消息不显示时间戳。
```

替换为：

```markdown
- 刷新后当前流式输出可能丢失：同一进程内前端会在数秒内通过 `current_turn_id` 重新订阅并重放当前轮缓冲；SSE 断线会自动探测轮次并按事件序号续传，不重复、不清空。进程重启时只能从 transcript 读回历史，执行过程从 thinking / tool_use 块尽量重建，信息少于实时流。消息不显示时间戳。
```

在 `## Chat` 的条目末尾加入：

```markdown
- 聊天细节：运行中的追问按提交顺序显示「排队中 N/M」并可单条撤回；停止后保留已生成的半截回复，历史已落盘则与原历史行收敛；切走会话或页面不可见时，完成/失败会在侧栏显示未读点（成功绿色、失败红色）并在标签标题显示计数，仅存在内存；侧栏会话行可重命名。
```

- [ ] **Step 3: 运行后端全量测试**

```bash
.venv/bin/python -m pytest apps/api/app/tests -q
```

Expected: 全部通过（此前基线为 803 passed, 3 skipped；新增用例后数量增加）。

- [ ] **Step 4: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: `✓ built in …`，`dist` 有更新时一并提交。

- [ ] **Step 5: 三次自我 code review**

按项目约定逐项检查并记录发现：

1. 第一遍：逐文件读 diff，检查队列撤回的顺序/原子性、SSE 序号与 after 边界、frozen 收敛、未读清除、改名失败路径。
2. 第二遍：检查与既有行为冲突（`/stop`、重启恢复、渠道投递、Agent 切换、虚拟化列表、memo 稳定性）。
3. 第三遍：检查测试覆盖与文档一致性；发现的问题立即修复并重跑测试/构建。

- [ ] **Step 6: 提交文档与构建产物**

```bash
git add CLAUDE.md README.md apps/web/dist
git commit -m "Document the polished chat behaviors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: 推送**

```bash
git push origin HEAD
```

---

## 手工验收清单

- [ ] 同一会话运行中连发三条追问：显示「排队中 1/3」「2/3」「3/3」；撤回第二条后第一条和第三条按顺序执行，第三条的序号刷新为 2/2。
- [ ] 撤回正在执行的 turn 返回 `cancelled:false`，界面提示「这条已经开始或已经结束，队列已刷新」，当前任务继续。
- [ ] 长任务中断网 3–5 秒：出现重连提示，恢复后流式文本不重复、不缺字。
- [ ] `/stop` 后：历史有半截回复则显示历史行；没有则显示「已停止」且不清空；有幸存排队追问时新 turn 从干净气泡开始。
- [ ] 在会话 B 查看时会话 A 完成：A 出现绿色未读点，标题 `(1) Agent Command Center`；点开 A 后清除。失败显示红色。
- [ ] 当前会话执行中切走标签页，完成后未读出现；切回浏览器当前会话未读清除。
- [ ] 侧栏铅笔改名：保存后侧栏与顶栏同步；刷新后保留；空标题不能保存。
- [ ] 后端全量测试与前端构建通过。
