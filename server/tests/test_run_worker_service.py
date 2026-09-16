"""Pin the RunWorkerService execution contract.

The platform runs exactly one Agent run at a time inside the FastAPI process and
deliberately does not support multiple uvicorn workers. That limit is a product
assumption, not an accident, so it is asserted here instead of being left as an
implicit side effect of `tick()`. The second contract is that a crashing run
never wedges the worker: the next tick must still claim the following run.
"""

import asyncio

from server.app.run_service import RunService
from server.app.run_worker_service import RunWorkerService
from server.app.system_log_service import SystemLogService


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
"""


def _build(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    service = RunService(tmp_path)
    worker = RunWorkerService(
        run_service=service,
        system_log_service=SystemLogService(tmp_path),
        worker_id="worker-a",
        poll_interval_seconds=0.01,
    )
    return service, worker


def _platform_log_text(workspace) -> str:
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return ""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(logs_dir.glob("platform-*.log"))
    )


async def _settle(worker) -> None:
    """Let the worker finish its currently claimed task."""
    for _ in range(200):
        if not worker._active_tasks:
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.01)


def test_run_worker_executes_one_run_at_a_time(tmp_path, monkeypatch) -> None:
    service, worker = _build(tmp_path)
    calls: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute(run_id, *, lease_id="") -> None:
        calls.append(run_id)
        started.set()
        await release.wait()

    monkeypatch.setattr(service, "execute_run", fake_execute)

    async def scenario() -> None:
        first = (await service.create_run(content="first", agent_id="assistant"))["run_id"]
        second = (await service.create_run(content="second", agent_id="assistant"))["run_id"]

        await worker.tick()
        await asyncio.wait_for(started.wait(), timeout=5)
        assert len(calls) == 1, calls
        assert service.get_run(calls[0])["state"]["status"] == "running"

        # A second tick must not claim another run while one is still executing.
        await worker.tick()
        assert len(calls) == 1, calls

        pending = second if calls[0] == first else first
        assert service.get_run(pending)["state"]["status"] == "queued"
        assert not (tmp_path / "runs" / pending / "lock.json").exists()

        release.set()
        await _settle(worker)

        await worker.tick()
        await _settle(worker)
        assert len(calls) == 2, calls

    asyncio.run(scenario())


def test_run_worker_keeps_claiming_after_execution_error(tmp_path, monkeypatch) -> None:
    service, worker = _build(tmp_path)
    calls: list[str] = []

    async def failing_execute(run_id, *, lease_id="") -> None:
        calls.append(run_id)
        raise RuntimeError("runtime exploded")

    monkeypatch.setattr(service, "execute_run", failing_execute)

    async def scenario() -> None:
        first = (await service.create_run(content="first", agent_id="assistant"))["run_id"]
        second = (await service.create_run(content="second", agent_id="assistant"))["run_id"]

        await worker.tick()
        await _settle(worker)
        assert len(calls) == 1

        await worker.tick()
        await _settle(worker)
        assert len(calls) == 2, calls
        assert set(calls) == {first, second}

    asyncio.run(scenario())

    assert "runtime exploded" in _platform_log_text(tmp_path)
