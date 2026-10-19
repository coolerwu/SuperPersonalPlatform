import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, Message, Run, Session
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
        agent = await db.get(Agent, session.agent_id)
        assert agent is not None
        agent.workspace_mode = "directory"
        agent.workspace_path = "worker-projected"
        await db.commit()
        agent_id = agent.id

    run, _ = await create_run(session_id, "hi")
    worker = RunWorker()
    await worker.execute_run(run.id)

    assert captured["workspace_mode"] == "directory"
    assert captured["workspace_path"] == "worker-projected"

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        agent.workspace_mode = "virtual"
        agent.workspace_path = ""
        await db.commit()


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
