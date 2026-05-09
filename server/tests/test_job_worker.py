import asyncio
import json
from pathlib import Path

from server.app.job_service import JobService
from server.app.job_worker import JobWorker


def write_task(workspace: Path, task_id: str, status: str = "created") -> None:
    task_dir = workspace / "self-dev" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    task_dir.joinpath("task.json").write_text(
        json.dumps(
            {
                "id": task_id,
                "goal": "update README",
                "agent_id": "assistant",
                "repo_url": "https://example.test/repo.git",
                "branch": f"agent/self-dev-{task_id}",
                "status": status,
                "repo_path": str(task_dir / "repo"),
                "created_at": "2026-05-09T00:00:00+00:00",
                "updated_at": "2026-05-09T00:00:00+00:00",
                "result": "",
                "error": "",
                "recommendation": "",
            }
        ),
        encoding="utf-8",
    )


def read_task(workspace: Path, task_id: str) -> dict[str, object]:
    return json.loads(
        (workspace / "self-dev" / "tasks" / task_id / "task.json").read_text(encoding="utf-8")
    )


class SlowSelfDevService:
    async def _run_task_internal(self, task_id: str, allow_push: bool = False, instruction: str = ""):
        await asyncio.sleep(60)


def test_job_worker_requeues_active_job_on_shutdown(tmp_path) -> None:
    async def scenario() -> None:
        write_task(tmp_path, "task-1")
        job_service = JobService(tmp_path)
        job_service.enqueue("task-1", "self_dev.run_task", {})
        worker = JobWorker(job_service, SlowSelfDevService(), poll_interval_seconds=0.01)

        claimed = worker.claim_once()
        assert claimed is not None
        await asyncio.sleep(0)

        await worker.stop()

        raw = read_task(tmp_path, "task-1")
        assert raw["status"] == "queued"
        assert raw["job"]["status"] == "queued"
        assert raw["job"]["lease"] is None

    asyncio.run(scenario())
