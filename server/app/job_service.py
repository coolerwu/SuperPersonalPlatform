from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from server.domain.jobs import Job, JobLease, JobStatus


ACTIVE_STATUSES = {JobStatus.QUEUED, JobStatus.RUNNING}
FINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class JobService:
    """Directory-backed job store for self-dev tasks.

    Job state is embedded in each workspace/self-dev/tasks/{task_id}/task.json file
    so existing task persistence remains the single durable store.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._tasks_dir = workspace / "self-dev" / "tasks"

    def enqueue(self, task_id: str, job_type: str, payload: dict[str, Any] | None = None) -> Job:
        task = self._read_task(task_id)
        existing = self._job_from_task(task)
        if existing and existing.status in ACTIVE_STATUSES:
            return existing

        job = Job.create(task_id, job_type, payload)
        self._write_task_with_job(task_id, task, job, task_status=JobStatus.QUEUED.value)
        return job

    def claim(self, job_type: str, worker_id: str) -> Job | None:
        for task_path in sorted(self._tasks_dir.glob("*/task.json")):
            try:
                task = self._read_task(task_path.parent.name)
                job = self._job_from_task(task)
            except Exception:
                continue
            if job is None or job.type != job_type or job.status != JobStatus.QUEUED:
                continue
            claimed = self._replace_job(
                job,
                status=JobStatus.RUNNING,
                lease=JobLease(worker_id=worker_id, leased_at=self._now()),
                attempts=job.attempts + 1,
            )
            self._write_task_with_job(job.task_id, task, claimed, task_status="running")
            return claimed
        return None

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | str | None = None,
        result: dict[str, Any] | None = None,
        clear_lease: bool = False,
        task_status: str | None = None,
    ) -> Job:
        task_id, task, job = self._find_job(job_id)
        changes: dict[str, Any] = {}
        if status is not None:
            changes["status"] = JobStatus(str(status))
        if result is not None:
            changes["result"] = result
        if clear_lease:
            changes["lease"] = None
        updated = self._replace_job(job, **changes)
        self._write_task_with_job(task_id, task, updated, task_status=task_status)
        return updated

    def cancel(self, job_id: str, reason: str = "") -> Job:
        return self.update(
            job_id,
            status=JobStatus.CANCELLED,
            result={"reason": reason},
            clear_lease=True,
            task_status="cancelled",
        )

    def list_active(self) -> list[Job]:
        jobs: list[Job] = []
        for task_path in sorted(self._tasks_dir.glob("*/task.json")):
            try:
                task = self._read_task(task_path.parent.name)
                job = self._job_from_task(task)
            except Exception:
                continue
            if job and job.status in ACTIVE_STATUSES:
                jobs.append(job)
        return jobs

    def requeue_interrupted_running_jobs(self) -> list[Job]:
        requeued: list[Job] = []
        for task_path in sorted(self._tasks_dir.glob("*/task.json")):
            try:
                task = self._read_task(task_path.parent.name)
                job = self._job_from_task(task)
            except Exception:
                continue
            if job and job.status == JobStatus.RUNNING:
                queued = self._replace_job(job, status=JobStatus.QUEUED, lease=None)
                self._write_task_with_job(job.task_id, task, queued, task_status="queued")
                requeued.append(queued)
        return requeued

    def _find_job(self, job_id: str) -> tuple[str, dict[str, Any], Job]:
        for task_path in self._tasks_dir.glob("*/task.json"):
            task_id = task_path.parent.name
            task = self._read_task(task_id)
            job = self._job_from_task(task)
            if job and job.id == job_id:
                return task_id, task, job
        raise FileNotFoundError(f"job not found: {job_id}")

    def _read_task(self, task_id: str) -> dict[str, Any]:
        if "/" in task_id or ".." in task_id:
            raise ValueError("invalid task id")
        return json.loads((self._tasks_dir / task_id / "task.json").read_text(encoding="utf-8"))

    def _write_task_with_job(
        self,
        task_id: str,
        task: dict[str, Any],
        job: Job,
        *,
        task_status: str | None = None,
    ) -> None:
        next_task = dict(task)
        next_task["job"] = job.to_dict()
        if task_status is not None:
            next_task["status"] = task_status
        next_task["updated_at"] = self._now()
        path = self._tasks_dir / task_id / "task.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(next_task, ensure_ascii=False, indent=2), encoding="utf-8")

    def _job_from_task(self, task: dict[str, Any]) -> Job | None:
        raw = task.get("job")
        if not isinstance(raw, dict):
            return None
        return Job.from_dict(raw, task_id=str(task.get("id") or ""))

    def _replace_job(self, job: Job, **changes: Any) -> Job:
        raw = job.to_dict()
        raw.update(changes)
        raw["updated_at"] = self._now()
        return Job.from_dict(raw, task_id=job.task_id)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
