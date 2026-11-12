# Stability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three audit-confirmed reliability gaps: channel messages queued behind a running turn must not be silently lost on restart/shutdown, cancellation must be bound to the turn it targets, and client disconnect must wait for the SDK's own shutdown escalation instead of orphaning the CLI process.

**Architecture:** Channel intake writes a durable `queued` delivery row before the turn is submitted; the runner promotes it to `pending` when the turn actually starts, and startup answers every still-`queued` row with the existing "not processed, please resend" text. Turns track their active turn id so a late interrupt cannot hit the next turn, and registry workers in the deferred-eviction teardown are protected from a user stop. The disconnect budget is raised above the SDK's ~20 s close escalation. No frontend changes.

**Tech Stack:** FastAPI, asyncio, SQLAlchemy async, pytest. All commands run from the repository root `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.

---

## File Structure

- `apps/api/app/services/channel_delivery.py` — `queued` status, `mark_started_in`, queued-row startup fill, discard coverage.
- `apps/api/app/services/channel_intake.py` — create the queued row before submitting a channel turn; discard it if submit fails.
- `apps/api/app/services/claude_runner.py` — promote a pre-created delivery row to `pending`; track active turn ids; keep deferred eviction on cancellation; raise the disconnect budget.
- `apps/api/app/services/recovery.py` — answer queued deliveries at startup.
- `apps/api/app/services/session_registry.py` — discard queued deliveries on user stop; pass the running turn id; protect finishing workers.
- Tests: `apps/api/app/tests/test_channel_delivery_service.py`, `test_channel_intake.py`, `test_recovery.py`, `test_session_registry.py`, `test_claude_runner.py`.
- Docs: `docs/intent.md`, `CLAUDE.md`, `README.md`.

---

### Task 1: Queue channel delivery obligations durably

**Files:**
- Modify: `apps/api/app/services/channel_delivery.py`
- Modify: `apps/api/app/services/channel_intake.py`
- Modify: `apps/api/app/services/claude_runner.py:160-168`, `:575-600`
- Modify: `apps/api/app/services/recovery.py:142-205`
- Modify: `apps/api/app/services/session_registry.py`
- Test: `apps/api/app/tests/test_channel_delivery_service.py`, `test_channel_intake.py`, `test_recovery.py`, `test_session_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/app/tests/test_channel_intake.py` (it already imports `pytest`, `channel_delivery`, `SessionLocal`, `ChannelDelivery`, `select`, `delete`) an autouse cleanup so the delivery rows every ingest now creates cannot leak into other tests:

```python
@pytest.fixture(autouse=True)
async def _clean_deliveries():
    yield
    async with SessionLocal() as db:
        await db.execute(delete(ChannelDelivery))
        await db.commit()


@pytest.mark.asyncio
async def test_ingest_creates_a_queued_delivery_before_turn_starts():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    try:
        result = await _ingest(store, registry, message_ids=["pre-1"])
        assert result.session_id
        turn = registry.turns[0]
        assert turn.delivery is not None
        assert turn.delivery.delivery_id is not None
        row = await channel_delivery.get(turn.delivery.delivery_id)
        assert row is not None
        assert row.status == "queued"
        assert row.content == ""
        assert row.session_id == result.session_id
        assert row.channel_id == "ch1"
        assert row.external_id == "wx-1"
    finally:
        await _cleanup("ch1", ["pre-1"])


@pytest.mark.asyncio
async def test_submit_failure_discards_delivery_and_releases_claim():
    await init_db()
    store = FakeStore()
    registry = FakeRegistry()
    registry.submit_error = RuntimeError("deleting")
    try:
        with pytest.raises(RuntimeError):
            await _ingest(store, registry, message_ids=["fail-1"])
        assert await _rows("ch1", ["fail-1"]) == []
        async with SessionLocal() as db:
            rows = await db.scalars(
                select(ChannelDelivery).where(
                    ChannelDelivery.channel_id == "ch1",
                    ChannelDelivery.external_id == "wx-1",
                )
            )
            assert list(rows) == []
    finally:
        await _cleanup("ch1", ["fail-1"])
```

Add to `apps/api/app/tests/test_claude_runner_delivery.py`:

```python
@pytest.mark.asyncio
async def test_precreated_delivery_is_discarded_when_the_turn_never_starts(tmp_path):
    await init_db()
    session_id = "12121212-1212-1212-1212-121212121212"
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db, channel_id="ch1", external_id="wx", session_id=session_id
        )
        await db.commit()
    try:
        def unused_factory(options):
            raise AssertionError("client must not be created")

        runner = ClaudeTurnRunner(
            store=FakeStore(),
            home=tmp_path,
            client_factory=unused_factory,
        )
        turn = Turn(
            session_id=session_id,
            agent_id="missing",
            prompt="hi",
            source="wechat",
            delivery=DeliveryRequest("ch1", "wx", delivery_id),
        )
        outcome = await runner.run(turn, TurnStream())
        assert outcome.status == "failed"
        assert await channel_delivery.get(delivery_id) is None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()
```

Add to `apps/api/app/tests/test_channel_delivery_service.py`:

```python
@pytest.mark.asyncio
async def test_find_unfilled_ignores_queued_rows():
    await init_db()
    queued_id = pending_id = None
    try:
        async with SessionLocal() as db:
            queued_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-q", session_id="queued-session"
            )
            pending_id = await channel_delivery.create_pending(
                db, channel_id="ch1", external_id="wx-p", session_id="queued-session"
            )
            await db.commit()
        found = await channel_delivery.find_unfilled_for_session("queued-session")
        assert found is not None
        assert found.id == pending_id
        assert await channel_delivery.get(queued_id) is not None
    finally:
        await _cleanup(queued_id, pending_id)


@pytest.mark.asyncio
async def test_mark_started_promotes_only_a_queued_row():
    await init_db()
    delivery_id = None
    try:
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-s", session_id="s1"
            )
            await db.commit()
        async with SessionLocal() as db:
            assert await channel_delivery.mark_started_in(db, delivery_id) is True
            await db.commit()
        row = await channel_delivery.get(delivery_id)
        assert row is not None and row.status == "pending"
        async with SessionLocal() as db:
            assert await channel_delivery.mark_started_in(db, delivery_id) is False
            await db.rollback()
    finally:
        await _cleanup(delivery_id)


@pytest.mark.asyncio
async def test_fail_queued_for_session_answers_only_queued_rows():
    await init_db()
    queued_id = pending_id = None
    try:
        async with SessionLocal() as db:
            queued_id = await channel_delivery.create_queued(
                db, channel_id="ch1", external_id="wx-q", session_id="s1"
            )
            pending_id = await channel_delivery.create_pending(
                db, channel_id="ch1", external_id="wx-p", session_id="s1"
            )
            await db.commit()
        assert await channel_delivery.fail_queued_for_session(
            "s1", "lost", "queued turn lost"
        ) == 1
        row = await channel_delivery.get(queued_id)
        assert row is not None and row.status == "pending" and row.content == "lost"
        other = await channel_delivery.get(pending_id)
        assert other is not None and other.status == "pending" and other.content == ""
    finally:
        await _cleanup(queued_id, pending_id)
```

Add to `apps/api/app/tests/test_recovery.py` (add `import uuid` at the top):

```python
@pytest.mark.asyncio
async def test_queued_delivery_is_answered_at_startup():
    await init_db()
    session_id = str(uuid.uuid4())
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_queued(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id=session_id,
        )
        await db.commit()
    try:
        await recovery.recover_running_sessions(FakeRegistry(), FakeStore())
        row = await channel_delivery.get(delivery_id)
        assert row is not None
        assert row.status == "pending"
        assert row.content == recovery.LOST_TURN_TEXT
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()
```

Add to `apps/api/app/tests/test_session_registry.py` (add imports `from sqlalchemy import delete`, `from app.db import SessionLocal, init_db`, `from app.models import ChannelDelivery`, `from app.services import channel_delivery`, and `DeliveryRequest` to the existing `Turn` import):

```python
@pytest.mark.asyncio
async def test_interrupt_discards_a_queued_delivery():
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
    await registry.submit(queued)
    try:
        assert await registry.interrupt("s1") is True
        assert await channel_delivery.get(delivery_id) is None
    finally:
        runner.release("a")
        await wait_until(lambda: runner.finished == ["a"])
        await registry.close()


@pytest.mark.asyncio
async def test_close_keeps_a_queued_delivery_for_startup():
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
    await registry.submit(queued)
    try:
        await registry.close()
        assert await channel_delivery.get(delivery_id) is not None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(ChannelDelivery.id == delivery_id)
            )
            await db.commit()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_channel_intake.py::test_ingest_creates_a_queued_delivery_before_turn_starts \
  apps/api/app/tests/test_channel_intake.py::test_submit_failure_discards_delivery_and_releases_claim \
  apps/api/app/tests/test_channel_delivery_service.py::test_find_unfilled_ignores_queued_rows \
  apps/api/app/tests/test_channel_delivery_service.py::test_mark_started_promotes_only_a_queued_row \
  apps/api/app/tests/test_channel_delivery_service.py::test_fail_queued_for_session_answers_only_queued_rows \
  apps/api/app/tests/test_claude_runner_delivery.py::test_precreated_delivery_is_discarded_when_the_turn_never_starts \
  apps/api/app/tests/test_recovery.py::test_queued_delivery_is_answered_at_startup \
  apps/api/app/tests/test_session_registry.py::test_interrupt_discards_a_queued_delivery \
  apps/api/app/tests/test_session_registry.py::test_close_keeps_a_queued_delivery_for_startup -v
```

Expected: FAIL — `create_queued` / `mark_started_in` / `fail_queued_for_session` do not exist, ingest still creates pending rows, and recovery does not answer queued rows.

- [ ] **Step 3: Implement the queued delivery status**

In `apps/api/app/services/channel_delivery.py`:

Change the module docstring's first line to:

```python
"""Channel reply obligations.

Channel intake creates a ``queued`` row before submitting the turn; the runner
promotes it to ``pending`` when the turn actually starts. Rows are filled with
empty content at that point. An empty pending row is only claimed by a resumed
turn (``find_unfilled_for_session``); a row that already holds content is only
sent by the delivery worker/sweep. ``sent`` and ``failed`` are terminal and
never rewritten.

A crash while the turn is still queued leaves a ``queued`` row with no lease;
startup fills every queued row with the explicit "not processed, please resend"
text so the normal startup sweep delivers it. A crash after the turn started
leaves a lease plus a pending row; recovery resumes the turn and reattaches the
pending row.
"""
```

Add after `_DELIVERABLE_STATUSES`:

```python
_CANCELLABLE_STATUSES = ("queued", "pending", "attempting")
```

Add these functions after `create_pending`:

```python
async def create_queued(
    db: AsyncSession, *, channel_id: str, external_id: str, session_id: str
) -> int:
    """Create the durable obligation while the turn is still in the queue."""
    row = ChannelDelivery(
        channel_id=channel_id,
        external_id=external_id,
        session_id=session_id,
        status="queued",
    )
    db.add(row)
    await db.flush()
    return int(row.id)


async def mark_started_in(db: AsyncSession, delivery_id: int) -> bool:
    """Promote a queued row inside the turn's lease transaction."""
    result = await db.execute(
        update(ChannelDelivery)
        .where(
            ChannelDelivery.id == delivery_id,
            ChannelDelivery.status == "queued",
        )
        .values(status="pending", updated_at=utcnow())
    )
    return (result.rowcount or 0) > 0


async def list_queued_session_ids() -> list[str]:
    """Session ids that still have a queued (never started) obligation."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(ChannelDelivery.session_id)
            .where(
                ChannelDelivery.status == "queued",
                ChannelDelivery.content == "",
            )
            .distinct()
        )
        return [str(item) for item in rows.all()]


async def fail_queued_for_session(
    session_id: str, content: str, error: str
) -> int:
    """Answer every queued obligation whose in-memory turn was lost."""
    async with SessionLocal() as db:
        result = await db.execute(
            update(ChannelDelivery)
            .where(
                ChannelDelivery.session_id == session_id,
                ChannelDelivery.status == "queued",
                ChannelDelivery.content == "",
            )
            .values(
                status="pending",
                content=content,
                last_error=str(error)[:2000],
                updated_at=utcnow(),
            )
        )
        await db.commit()
        return int(result.rowcount or 0)
```

In `discard`, change the status filter from `_DELIVERABLE_STATUSES` to `_CANCELLABLE_STATUSES`:

```python
            delete(ChannelDelivery).where(
                ChannelDelivery.id == delivery_id,
                ChannelDelivery.status.in_(_CANCELLABLE_STATUSES),
            )
```

`find_unfilled_for_session`, `list_unfilled_session_ids`, `list_pending`, `mark_attempting`, `complete`, and `fail_unfilled_for_session` keep using `_DELIVERABLE_STATUSES`; queued rows are intentionally invisible to them.

- [ ] **Step 4: Create the queued row at ingest and promote it at turn start**

In `apps/api/app/services/channel_intake.py`, add the import:

```python
from app.services import channel_delivery
```

At the start of `ingest`, before `try:`, add:

```python
    delivery_id: int | None = None
```

Replace the `registry.submit(...)` block in the non-command path with:

```python
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_queued(
                db,
                channel_id=channel_id,
                external_id=external_id,
                session_id=conversation.session_id,
            )
            await db.commit()
        await registry.submit(
            Turn(
                session_id=conversation.session_id,
                agent_id=agent.id,
                prompt=text,
                source="wechat",
                delivery=DeliveryRequest(
                    channel_id=channel_id,
                    external_id=external_id,
                    delivery_id=delivery_id,
                ),
            )
        )
```

In the existing `except BaseException:` block, before `_release_inbound`, add:

```python
        if delivery_id is not None:
            try:
                await channel_delivery.discard(delivery_id)
            except Exception:
                logger.warning(
                    "failed to discard queued delivery=%s",
                    delivery_id,
                    exc_info=True,
                )
```

In `apps/api/app/services/claude_runner.py`, replace `DeliveryRequest`:

```python
@dataclass
class DeliveryRequest:
    """Channel obligation owned by this turn.

    Channel intake pre-creates a queued row and passes its id; the runner
    promotes it when the turn starts. Legacy callers (cron) leave
    ``delivery_id`` unset and the runner creates a pending row at turn start.
    """

    channel_id: str
    external_id: str
    delivery_id: int | None = None
```

In `ClaudeTurnRunner.run`, after the `agent is None` check and before the lease transaction, add:

```python
                if turn.delivery is not None and turn.source != "resume":
                    delivery_id = turn.delivery.delivery_id
```

Change the transaction body to:

```python
                async with SessionLocal() as db:
                    await turn_lease.add_in(db, turn.session_id)
                    if delivery_id is not None:
                        # Promote queued -> pending in the same transaction as
                        # the lease; a crash before this leaves the row queued
                        # and startup answers it.
                        if not await channel_delivery.mark_started_in(
                            db, delivery_id
                        ):
                            delivery_id = None
                    elif (
                        turn.delivery is not None
                        and turn.source != "resume"
                    ):
                        # Legacy callers (cron) still create the obligation at
                        # turn start.
                        delivery_id = await channel_delivery.create_pending(
                            db,
                            channel_id=turn.delivery.channel_id,
                            external_id=turn.delivery.external_id,
                            session_id=turn.session_id,
                        )
                    await db.commit()
```

In `ClaudeTurnRunner.run`'s `finally`, next to the existing delivery teardown block, add:

```python
            if (
                delivery_id is None
                and not keep_lease
                and turn.delivery is not None
                and turn.delivery.delivery_id is not None
            ):
                # The turn never attached (agent missing, stopped before the
                # lease, ...): the queued obligation must not outlive it.
                await _best_effort(
                    lambda: channel_delivery.discard(turn.delivery.delivery_id),
                    f"queued delivery discard delivery={turn.delivery.delivery_id}",
                )
```

- [ ] **Step 5: Answer queued rows at startup**

In `apps/api/app/services/recovery.py`, add to `recover_running_sessions` after the lease loop and before `_answer_orphan_deliveries`:

```python
    try:
        await _answer_queued_deliveries()
    except Exception:
        logger.exception("recovery queued delivery backfill failed")
```

Add the helper above `recover_running_sessions`:

```python
async def _answer_queued_deliveries() -> int:
    """Answer every obligation whose turn never left the in-memory queue."""
    try:
        session_ids = await channel_delivery.list_queued_session_ids()
    except Exception:
        logger.exception("recovery could not list queued deliveries")
        return 0
    answered = 0
    for session_id in session_ids:
        try:
            answered += await channel_delivery.fail_queued_for_session(
                session_id,
                LOST_TURN_TEXT,
                "queued turn lost on restart",
            )
        except Exception:
            logger.exception(
                "recovery could not answer queued delivery session=%s", session_id
            )
    if answered:
        logger.warning("recovery answered %d queued channel delivery row(s)", answered)
    return answered
```

- [ ] **Step 6: Discard queued rows on user stop, keep them on shutdown**

In `apps/api/app/services/session_registry.py`, add the import:

```python
from app.services import channel_delivery
```

Add after the exception classes:

```python
async def _discard_delivery(turn: Turn) -> None:
    """Drop a queued turn's pre-created delivery when the user stops it."""
    delivery = getattr(turn, "delivery", None)
    delivery_id = getattr(delivery, "delivery_id", None) if delivery else None
    if delivery_id is None:
        return
    try:
        await channel_delivery.discard(delivery_id)
    except Exception:
        logger.warning(
            "failed to discard cancelled delivery=%s", delivery_id, exc_info=True
        )
```

In `interrupt`, collect the turns dropped from the pending slot and the queue:

```python
    async def interrupt(self, session_id: str) -> bool:
        cancelled = False
        dropped: list[Turn] = []
        pending = self._pending.get(session_id)
        if pending is not None and session_id not in self._started:
            pending_turn, stream = pending
            dropped.append(pending_turn)
            self._cancelled.add(pending_turn.id)
            stream.publish({"type": "turn_end", "status": "cancelled"})
            stream.close()
            cancelled = True
        queue = self._queues.get(session_id)
        if queue is not None:
            while True:
                try:
                    queued_turn, stream = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                dropped.append(queued_turn)
                stream.publish({"type": "turn_end", "status": "cancelled"})
                stream.close()
                queue.task_done()
                cancelled = True
        for dropped_turn in dropped:
            await _discard_delivery(dropped_turn)
        if cancelled and session_id not in self._running:
            # The worker is idle (or never started) and would otherwise stay
            # blocked on an empty queue forever. Retire it before any await.
            worker = self._workers.pop(session_id, None)
            if worker is not None:
                worker.cancel()
            self._queues.pop(session_id, None)
            self._current.pop(session_id, None)
```

Do **not** call `_discard_delivery` in `close()`; queued rows must survive for startup backfill.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_channel_delivery_service.py \
  apps/api/app/tests/test_channel_intake.py \
  apps/api/app/tests/test_recovery.py \
  apps/api/app/tests/test_session_registry.py \
  apps/api/app/tests/test_claude_runner_delivery.py -q
```

Expected: all pass.

- [ ] **Step 8: Update docs**

- `docs/intent.md` F2 item 5: append: 「渠道消息在入站时先写 `queued` 投递行再入队；turn 开始时提升为 `pending`。排队期间服务重启或优雅关闭时，启动后由 `_answer_queued_deliveries` 填入「服务重启时这条消息尚未开始处理，请重新发送」并发送；用户 `/stop` 会丢弃排队 turn 的投递行。」
- `docs/intent.md` F6 channel section: append: 「入站消息在入队前创建 `queued` 投递行；排队中重启由启动兜底答复，不会静默丢失。」
- `CLAUDE.md` channel bullet: append: 「渠道消息在入队前创建 `queued` 投递行，turn 开始时提升为 `pending`；排队中重启由启动兜底答复，`/stop` 丢弃排队 turn 的投递行。」
- `README.md` channel section: append: 「渠道消息在入队前创建 `queued` 投递行；排队中重启会由启动兜底发送「尚未开始处理，请重新发送」。」
- `apps/api/app/services/channel_delivery.py` module docstring: replace with the queued-status text shown in Step 3.

- [ ] **Step 9: Commit**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git add apps/api/app/services/channel_delivery.py \
        apps/api/app/services/channel_intake.py \
        apps/api/app/services/claude_runner.py \
        apps/api/app/services/recovery.py \
        apps/api/app/services/session_registry.py \
        apps/api/app/tests/test_channel_delivery_service.py \
        apps/api/app/tests/test_channel_intake.py \
        apps/api/app/tests/test_recovery.py \
        apps/api/app/tests/test_session_registry.py \
        docs/intent.md CLAUDE.md README.md \
        docs/superpowers/plans/2026-11-12-stability-hardening.md
git commit -m "Queue channel delivery obligations durably"
```

---

### Task 2: Bind cancellation to the active turn and protect finishing workers

**Files:**
- Modify: `apps/api/app/services/claude_runner.py:228-235` (state), `:560-575`, `:780-840` (interrupt), `:860-875` (after_turn)
- Modify: `apps/api/app/services/session_registry.py:46-80` (state), `:125-176` (worker finally), `:274-310` (interrupt)
- Test: `apps/api/app/tests/test_claude_runner.py`, `apps/api/app/tests/test_session_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/app/tests/test_claude_runner.py`:

```python
@pytest.mark.asyncio
async def test_interrupt_with_a_stale_turn_id_is_ignored(tmp_path):
    await init_db()
    client = HangingClient()
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: client,
    )
    turn = make_turn(session_id="abababab-abab-abab-abab-abababababab")
    stream = TurnStream()
    task = asyncio.create_task(runner.run(turn, stream))
    await asyncio.wait_for(client.queried.wait(), 1)
    try:
        assert (
            await runner.interrupt(
                turn.session_id, turn.agent_id, turn_id="stale-turn"
            )
            is False
        )
        assert client.interrupted is False
        assert (
            await runner.interrupt(
                turn.session_id, turn.agent_id, turn_id=turn.id
            )
            is True
        )
        assert client.interrupted is True
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await turn_lease.remove(turn.session_id)


@pytest.mark.asyncio
async def test_after_turn_keeps_pending_eviction_when_cancelled(tmp_path, monkeypatch):
    runner = ClaudeTurnRunner(store=FakeStore(make_agent()), home=tmp_path)
    key = ("default", "session")

    async def cancelled_evict(*args, **kwargs):
        raise asyncio.CancelledError()

    runner._evict_pending.add(key)
    monkeypatch.setattr(runner, "_evict_key", cancelled_evict)
    with pytest.raises(asyncio.CancelledError):
        await runner.after_turn("session", "default")
    assert key in runner._evict_pending
```

In `apps/api/app/tests/test_session_registry.py`, extend `FakeRunner`:

```python
    async def interrupt(self, session_id, agent_id=None, turn_id=None):
        self.interrupted.append(session_id)
        self.interrupt_turn_ids.append(turn_id)
        return self.interrupt_result
```

Add `self.interrupt_turn_ids: list[str | None] = []` in `FakeRunner.__init__`.

Add:

```python
@pytest.mark.asyncio
async def test_interrupt_passes_the_running_turn_id():
    runner = FakeRunner()
    registry = SessionRegistry(runner, concurrency=2)
    turn_id = await registry.submit(turn(prompt="a"))
    await wait_until(lambda: runner.started == ["a"])

    assert await registry.interrupt("s1") is True
    assert runner.interrupt_turn_ids == [turn_id]

    runner.release("a")
    await wait_until(lambda: runner.finished == ["a"])
    await registry.close()


class SlowAfterTurnRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.after_started = asyncio.Event()
        self.after_release = asyncio.Event()

    async def after_turn(self, session_id, agent_id=None):
        self.after_started.set()
        await self.after_release.wait()
        self.after_turns.append((session_id, agent_id))


@pytest.mark.asyncio
async def test_interrupt_does_not_cancel_a_finishing_worker():
    runner = SlowAfterTurnRunner()
    registry = SessionRegistry(runner, concurrency=1)
    await registry.submit(turn(prompt="a"))
    runner.release("a")
    await wait_until(lambda: runner.after_started.is_set())

    await registry.submit(turn(prompt="b"))
    assert await registry.interrupt("s1") is True

    runner.after_release.set()
    await wait_until(lambda: runner.after_turns == [("s1", "default")])
    await wait_until(lambda: not registry._workers)
    assert runner.started == ["a"]
    await registry.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_claude_runner.py::test_interrupt_with_a_stale_turn_id_is_ignored \
  apps/api/app/tests/test_claude_runner.py::test_after_turn_keeps_pending_eviction_when_cancelled \
  apps/api/app/tests/test_session_registry.py::test_interrupt_passes_the_running_turn_id \
  apps/api/app/tests/test_session_registry.py::test_interrupt_does_not_cancel_a_finishing_worker -v
```

Expected: FAIL — the runner's `interrupt` has no `turn_id`, `after_turn` discards the key before eviction, and the registry cancels the finishing worker.

- [ ] **Step 3: Implement turn-id tracking in the runner**

In `ClaudeTurnRunner.__init__`, after `self._active`:

```python
        # Turn id currently attached to each cached client key. /stop uses it
        # so a late interrupt cannot hit the session's next turn.
        self._active_turns: dict[ClientKey, str] = {}
```

In `run`, after `self._active.add(key)`:

```python
        self._active_turns[key] = turn.id
```

In the `finally`, next to `self._active.discard(key)`:

```python
            if self._active_turns.get(key) == turn.id:
                self._active_turns.pop(key, None)
```

- [ ] **Step 4: Guard interrupt by turn id**

Change the signature and matching logic in `ClaudeTurnRunner.interrupt`:

```python
    async def interrupt(
        self,
        session_id: str,
        agent_id: str | None = None,
        turn_id: str | None = None,
    ) -> bool:
        lock = self._client_lock(session_id)
        keys = [
            key
            for key in self._keys_for_session(session_id)
            if agent_id is None or key[0] == agent_id
        ]
        if agent_id is not None and not keys:
            keys = [(agent_id, session_id)]

        def matching() -> list[ClientKey]:
            return [
                key
                for key in keys
                if key in self._active
                and (turn_id is None or self._active_turns.get(key) == turn_id)
            ]

        try:
            async with asyncio.timeout(_INTERRUPT_LOCK_TIMEOUT_SECONDS):
                async with lock:
                    active = matching()
                    if not active:
                        return False
                    for key in active:
                        self._cancel_requested.add(key)
        except TimeoutError:
            # The session may have started or ended while this call waited.
            keys = [
                key
                for key in self._keys_for_session(session_id)
                if agent_id is None or key[0] == agent_id
            ]
            if agent_id is not None and not keys:
                keys = [(agent_id, session_id)]
            active = matching()
            logger.warning(
                "interrupt lock timed out session=%s agent=%s",
                session_id,
                agent_id,
            )
            if not active:
                return False
            for key in active:
                self._cancel_requested.add(key)
        client = next(
            (
                cached.client
                for key, cached in self._clients.items()
                if key[1] == session_id
                and (agent_id is None or key[0] == agent_id)
                and (turn_id is None or self._active_turns.get(key) == turn_id)
            ),
            None,
        )
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), timeout=5)
            except Exception:
                logger.warning(
                    "interrupt failed session=%s", session_id, exc_info=True
                )
        return True
```

- [ ] **Step 5: Keep deferred eviction on cancellation**

Change `after_turn` to discard the pending key only after `_evict_key` completes:

```python
    async def after_turn(self, session_id: str, agent_id: str | None = None) -> None:
        """Drain evictions deferred by ``run`` after the permit is released."""
        keys = [
            key
            for key in list(self._evict_pending)
            if key[1] == session_id and (agent_id is None or key[0] == agent_id)
        ]
        for key in keys:
            try:
                await self._evict_key(key)
            except asyncio.CancelledError:
                # Keep the key for the next after_turn; the client was not
                # evicted and must not be reused silently.
                raise
            self._evict_pending.discard(key)
```

- [ ] **Step 6: Pass the turn id and protect finishing workers in the registry**

In `SessionRegistry.__init__`, after `self._running`:

```python
        # Workers in the after_turn teardown (deferred eviction) are not
        # cancellable by a user stop; cancelling them would drop the eviction.
        self._finishing: set[str] = set()
```

In `_session_loop`'s `finally`, wrap `after_turn`:

```python
                queue.task_done()
                self._finishing.add(session_id)
                try:
                    # A slow CLI disconnect must not hold the concurrency permit;
                    # the runner deferred it until now.
                    after_turn = getattr(self._runner, "after_turn", None)
                    if after_turn is not None:
                        try:
                            await after_turn(turn.session_id, turn.agent_id)
                        except Exception:
                            logger.exception(
                                "deferred client eviction failed session=%s",
                                session_id,
                            )
                finally:
                    self._finishing.discard(session_id)
```

In `interrupt`, refuse to retire a finishing worker:

```python
        if (
            cancelled
            and session_id not in self._running
            and session_id not in self._finishing
        ):
```

Pass the current turn id:

```python
        active = await self._runner.interrupt(
            session_id,
            self._session_agents.get(session_id),
            turn_id=self._current.get(session_id),
        )
```

In `close`, clear the new set in the final `finally`:

```python
            self._finishing.clear()
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_claude_runner.py \
  apps/api/app/tests/test_session_registry.py -q
```

Expected: all pass, including the existing interrupt tests.

- [ ] **Step 8: Commit**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git add apps/api/app/services/claude_runner.py \
        apps/api/app/services/session_registry.py \
        apps/api/app/tests/test_claude_runner.py \
        apps/api/app/tests/test_session_registry.py
git commit -m "Bind interrupts to the active turn and protect finishing workers"
```

---

### Task 3: Wait for the SDK's own disconnect escalation

**Files:**
- Modify: `apps/api/app/services/claude_runner.py:50-64` (constants and comments)
- Modify: `CLAUDE.md:17`
- Test: `apps/api/app/tests/test_claude_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/app/tests/test_claude_runner.py`:

```python
def test_disconnect_budget_covers_the_sdk_close():
    assert claude_runner._DISCONNECT_TIMEOUT_SECONDS >= 20
    assert (
        claude_runner._SHUTDOWN_BUDGET_SECONDS
        > claude_runner._DISCONNECT_TIMEOUT_SECONDS
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_claude_runner.py::test_disconnect_budget_covers_the_sdk_close -v
```

Expected: FAIL with `AssertionError` (current values 10 / 20).

- [ ] **Step 3: Raise the budget**

In `apps/api/app/services/claude_runner.py`, replace the constants and comments:

```python
# The SDK's transport close closes stdin, waits, sends SIGTERM, waits, then
# SIGKILL, with roughly a 20 s worst-case bound. An asyncio.timeout shorter
# than that raw-cancels the SDK's shielded cleanup and skips the escalation,
# so wait longer than the SDK itself before treating a close as an orphan.
_DISCONNECT_TIMEOUT_SECONDS = 25
# Total time ``close_all`` may spend disconnecting cached clients. The deploy
# unit allows 60 s for the whole shutdown; ``SessionRegistry.close`` may take
# up to 10 s, leaving room for the other runtimes.
_SHUTDOWN_BUDGET_SECONDS = 30
```

- [ ] **Step 4: Run the test and the shutdown tests**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest \
  apps/api/app/tests/test_claude_runner.py::test_disconnect_budget_covers_the_sdk_close \
  apps/api/app/tests/test_claude_runner.py::test_close_all_bounds_each_disconnect_and_keeps_orphans \
  apps/api/app/tests/test_claude_runner.py::test_idle_eviction_timeout_retains_an_orphan_reference -q
```

Expected: PASS. The existing tests monkeypatch the constants, so they stay fast.

- [ ] **Step 5: Update the architecture note**

In `CLAUDE.md`, the execution-core bullet currently ends with the client cache/eviction description. Append:

```text
断开每个 client 最多等 25 秒（SDK 自身约 20 秒的 SIGTERM→SIGKILL 升级），关机总预算 30 秒。
```

- [ ] **Step 6: Commit**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git add apps/api/app/services/claude_runner.py \
        apps/api/app/tests/test_claude_runner.py \
        CLAUDE.md
git commit -m "Wait for the SDK disconnect escalation before orphaning a client"
```

---

## Final verification

- [ ] Run the full backend suite:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
.venv/bin/python -m pytest apps/api/app/tests
```

Expected: all tests pass.

- [ ] Run the frontend build (no frontend changes, but the project convention requires it before a deploy):

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform/apps/web && npm run build
```

Expected: build passes; `dist` is unchanged.

- [ ] Inspect the final diff:

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git log --oneline -4
```

Expected: three commits, clean tree.

## Known boundaries

- The `queued` row and the inbound claim are still separate commits inside one ingest request; a crash between the claim and the row insert (milliseconds) leaves a claimed inbound with no row. This plan removes the long queue-wait window, not that microsecond window.
- A queued channel turn cancelled by the user via `/stop` has its queued row deleted; a graceful shutdown keeps it so startup backfill can answer it.
- Cron push turns still create their delivery row at turn start (no pre-created `queued` row); a cron push queued behind a running turn can still be lost on restart. Extending the same pattern to `cron_service` is a follow-up.
- The hard SDK `interrupt()` has no per-turn id; the runner checks the active turn id immediately before sending it, but a sub-millisecond race between a turn ending and the control request remains theoretically possible.
- The 25 s disconnect wait applies per client; `close_all` still caps the whole shutdown at 30 s, so with many wedged clients later ones may be kept as orphans.
