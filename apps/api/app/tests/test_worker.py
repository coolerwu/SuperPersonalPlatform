import asyncio

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, Message, Run, RunEvent, Session, utcnow
from app.services.executor import ExecutorEvent
from app.services.run_service import create_run, requeue_running_runs
from app.services.worker import RunWorker


class FakeExecutor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def run(self, *, instructions: str, session_id: str, content: str):
        yield ExecutorEvent("assistant_delta", {"delta": "hello "})
        yield ExecutorEvent("assistant_delta", {"delta": "world"})
        yield ExecutorEvent(
            "model_usage",
            {"call_id": "fake-1", "model": "fake", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )
        yield ExecutorEvent(
            "run_completed",
            {"output": "hello world", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        )


async def _new_session() -> str:
    async with SessionLocal() as db:
        agent = await db.scalar(select(Agent).limit(1))
        assert agent is not None
        session = Session(agent_id=agent.id)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session.id


async def _cancel_queued_runs() -> None:
    async with SessionLocal() as db:
        runs = (await db.scalars(select(Run).where(Run.status == "queued"))).all()
        for run in runs:
            run.status = "cancelled"
            run.finished_at = utcnow()
        await db.commit()


@pytest.mark.asyncio
async def test_worker_completes_run(monkeypatch) -> None:
    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FakeExecutor)
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")
    worker = RunWorker()
    await worker.execute_run(run.id)

    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.output == "hello world"
        assert stored.total_tokens == 5
        messages = list(
            (await db.scalars(select(Message).where(Message.session_id == session_id).order_by(Message.created_at))).all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].content == "hello world"


@pytest.mark.asyncio
async def test_worker_closes_executor_stream(monkeypatch) -> None:
    closed: list[bool] = []

    class ClosingExecutor(FakeExecutor):
        async def run(self, *, instructions: str, session_id: str, content: str):
            try:
                yield ExecutorEvent("assistant_delta", {"delta": "hello"})
                yield ExecutorEvent(
                    "run_completed",
                    {
                        "output": "hello",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                )
            finally:
                closed.append(True)

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", ClosingExecutor)
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")
    worker = RunWorker()
    await worker.execute_run(run.id)

    assert closed == [True]


@pytest.mark.asyncio
async def test_worker_passes_workspace_config(monkeypatch) -> None:
    captured: dict = {}

    class CapturingExecutor(FakeExecutor):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            captured.update(self.kwargs)

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", CapturingExecutor)
    session_id = await _new_session()
    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        agent_id = session.agent_id
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        agent.enabled_skills = ["demo"]
        await db.commit()

    run, _ = await create_run(session_id, "hi")
    worker = RunWorker()
    await worker.execute_run(run.id)

    assert captured["workspace_path"] == agent_id
    assert captured["enabled_skills"] == ["demo"]


@pytest.mark.asyncio
async def test_worker_cancels_queued_run() -> None:
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")
    worker = RunWorker()
    assert await worker.cancel(run.id) is True
    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        assert stored.status == "cancelled"


@pytest.mark.asyncio
async def test_requeue_running_runs() -> None:
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")
    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        stored.status = "running"
        await db.commit()
    await requeue_running_runs()
    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        assert stored.status == "queued"


@pytest.mark.asyncio
async def test_worker_runs_different_sessions_in_parallel(monkeypatch) -> None:
    started: list[str] = []
    active = {"count": 0, "max": 0}
    gate = asyncio.Event()

    class GatedExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run(self, *, instructions: str, session_id: str, content: str):
            started.append(session_id)
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            try:
                await gate.wait()
                yield ExecutorEvent(
                    "run_completed",
                    {
                        "output": "ok",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                )
            finally:
                active["count"] -= 1

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", GatedExecutor)
    await _cancel_queued_runs()
    session_ids = [await _new_session() for _ in range(3)]
    runs = [(await create_run(session_id, "hi"))[0] for session_id in session_ids]

    worker = RunWorker()
    await worker.start()
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while len(started) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert len(started) == 2
        assert active["max"] == 2

        async with SessionLocal() as db:
            statuses = [(await db.get(Run, run.id)).status for run in runs]
        assert statuses.count("running") == 2
        assert statuses.count("queued") == 1

        gate.set()
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            async with SessionLocal() as db:
                statuses = [(await db.get(Run, run.id)).status for run in runs]
            if all(status == "completed" for status in statuses):
                break
            await asyncio.sleep(0.02)
        assert statuses == ["completed", "completed", "completed"]
        assert active["max"] == 2
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_keeps_one_run_per_session(monkeypatch) -> None:
    started: list[str] = []
    active = {"count": 0, "max": 0}
    gate = asyncio.Event()

    class GatedExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run(self, *, instructions: str, session_id: str, content: str):
            started.append(session_id)
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            try:
                await gate.wait()
                yield ExecutorEvent(
                    "run_completed",
                    {
                        "output": "ok",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                )
            finally:
                active["count"] -= 1

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", GatedExecutor)
    await _cancel_queued_runs()
    session_id = await _new_session()
    first, _ = await create_run(session_id, "one")

    async with SessionLocal() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        second = Run(
            agent_id=session.agent_id,
            session_id=session_id,
            input="two",
            status="queued",
        )
        db.add(second)
        await db.commit()
        await db.refresh(second)

    worker = RunWorker()
    await worker.start()
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while not started and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert started == [session_id]

        await asyncio.sleep(0.3)
        assert len(started) == 1
        assert active["max"] == 1
        async with SessionLocal() as db:
            stored = await db.get(Run, second.id)
            assert stored is not None
            assert stored.status == "queued"

        gate.set()
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            async with SessionLocal() as db:
                first_status = (await db.get(Run, first.id)).status
                second_status = (await db.get(Run, second.id)).status
            if first_status == second_status == "completed":
                break
            await asyncio.sleep(0.02)
        assert first_status == "completed"
        assert second_status == "completed"
        assert started == [session_id, session_id]
        assert active["max"] == 1
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_cancels_running_task(monkeypatch) -> None:
    started = asyncio.Event()

    class HangingExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run(self, *, instructions: str, session_id: str, content: str):
            started.set()
            yield ExecutorEvent("assistant_delta", {"delta": "waiting"})
            await asyncio.Event().wait()

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", HangingExecutor)
    await _cancel_queued_runs()
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")

    worker = RunWorker()
    await worker.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await worker.cancel(run.id) is True

        stored = None
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            async with SessionLocal() as db:
                stored = await db.get(Run, run.id)
            if stored is not None and stored.status == "cancelled":
                break
            await asyncio.sleep(0.02)
        assert stored is not None
        assert stored.status == "cancelled"
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_stop_requeues_running_run(monkeypatch) -> None:
    started = asyncio.Event()

    class HangingExecutor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run(self, *, instructions: str, session_id: str, content: str):
            started.set()
            yield ExecutorEvent("assistant_delta", {"delta": "working"})
            await asyncio.Event().wait()

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", HangingExecutor)
    await _cancel_queued_runs()
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")

    worker = RunWorker()
    await worker.start()
    await asyncio.wait_for(started.wait(), timeout=5)
    await worker.stop()

    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.started_at is None
        events = (
            await db.scalars(
                select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id)
            )
        ).all()
        assert any(
            event.type == "run_queued" and event.payload.get("reason") == "service_restart"
            for event in events
        )

    monkeypatch.setattr("app.services.worker.DeepAgentExecutor", FakeExecutor)
    recovered = RunWorker()
    await recovered.start()
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            async with SessionLocal() as db:
                stored = await db.get(Run, run.id)
            if stored is not None and stored.status == "completed":
                break
            await asyncio.sleep(0.02)
        assert stored is not None
        assert stored.status == "completed"
    finally:
        await recovered.stop()


@pytest.mark.asyncio
async def test_requeue_running_runs_respects_cancel_request() -> None:
    await _cancel_queued_runs()
    session_id = await _new_session()
    run, _ = await create_run(session_id, "hi")

    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        stored.status = "running"
        stored.cancel_requested = True
        await db.commit()

    await requeue_running_runs()

    async with SessionLocal() as db:
        stored = await db.get(Run, run.id)
        assert stored is not None
        assert stored.status == "cancelled"
