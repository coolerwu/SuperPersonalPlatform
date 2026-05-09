import json
from pathlib import Path

from server.app.job_service import JobService
from server.domain.jobs import JobStatus


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


def test_job_service_enqueue_persists_job_in_task_json(tmp_path) -> None:
    write_task(tmp_path, "task-1")
    service = JobService(tmp_path)

    job = service.enqueue("task-1", "self_dev.run_task", {"instruction": "go"})

    assert job.task_id == "task-1"
    assert job.status == JobStatus.QUEUED
    raw = read_task(tmp_path, "task-1")
    assert raw["status"] == "queued"
    assert raw["job"]["id"] == job.id
    assert raw["job"]["type"] == "self_dev.run_task"
    assert raw["job"]["payload"] == {"instruction": "go"}


def test_job_service_claim_rebuilds_from_task_directory_and_leases_job(tmp_path) -> None:
    write_task(tmp_path, "task-1")
    service = JobService(tmp_path)
    service.enqueue("task-1", "self_dev.run_task", {})

    restored = JobService(tmp_path)
    claimed = restored.claim("self_dev.run_task", worker_id="worker-a")

    assert claimed is not None
    assert claimed.task_id == "task-1"
    assert claimed.status == JobStatus.RUNNING
    assert claimed.lease is not None
    assert claimed.lease.worker_id == "worker-a"
    assert claimed.attempts == 1
    raw = read_task(tmp_path, "task-1")
    assert raw["status"] == "running"
    assert raw["job"]["status"] == "running"
    assert raw["job"]["attempts"] == 1


def test_job_service_cancel_updates_task_and_job_status(tmp_path) -> None:
    write_task(tmp_path, "task-1")
    service = JobService(tmp_path)
    job = service.enqueue("task-1", "self_dev.run_task", {})

    cancelled = service.cancel(job.id, reason="user cancelled")

    assert cancelled.status == JobStatus.CANCELLED
    raw = read_task(tmp_path, "task-1")
    assert raw["status"] == "cancelled"
    assert raw["job"]["result"] == {"reason": "user cancelled"}
