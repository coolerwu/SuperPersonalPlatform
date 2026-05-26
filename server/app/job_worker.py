from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from server.app.job_service import JobService
from server.app.self_dev_service import SelfDevService
from server.domain.jobs import Job, JobStatus


class JobWorker:
    """In-process worker that resumes and executes directory-backed jobs."""

    def __init__(
        self,
        job_service: JobService,
        self_dev_service: SelfDevService,
        *,
        poll_interval_seconds: float = 1.0,
        worker_id: str | None = None,
    ) -> None:
        self._job_service = job_service
        self._self_dev_service = self_dev_service
        self._poll_interval_seconds = poll_interval_seconds
        self._worker_id = worker_id or f"self-dev-worker-{uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._cancelled_job_ids: set[str] = set()
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._job_service.requeue_interrupted_running_jobs()
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._loop_task
        for task in list(self._active_jobs.values()):
            task.cancel()
        if self._active_jobs:
            await asyncio.gather(*self._active_jobs.values(), return_exceptions=True)

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            self.claim_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    def claim_once(self) -> Job | None:
        if self._active_jobs:
            return None
        job = self._job_service.claim("self_dev.run_task", self._worker_id)
        if job is None:
            return None
        task = asyncio.create_task(self._execute(job))
        self._active_jobs[job.id] = task
        task.add_done_callback(lambda _done: self._active_jobs.pop(job.id, None))
        return job

    def cancel_task(self, task_id: str, reason: str = "user cancelled") -> bool:
        for job in self._job_service.list_active():
            if job.task_id != task_id:
                continue
            self._cancelled_job_ids.add(job.id)
            active_task = self._active_jobs.get(job.id)
            if active_task is not None:
                active_task.cancel()
            self._job_service.cancel(job.id, reason=reason)
            return True
        return False

    async def _execute(self, job: Job) -> None:
        try:
            result_task = await self._self_dev_service._run_task_internal(
                job.task_id,
                bool(job.payload.get("allow_push")),
                str(job.payload.get("instruction") or ""),
            )
            final_status = JobStatus.SUCCEEDED if result_task.status != "failed" else JobStatus.FAILED
            self._job_service.update(
                job.id,
                status=final_status,
                result={"task_status": result_task.status},
                clear_lease=True,
            )
        except asyncio.CancelledError:
            if job.id in self._cancelled_job_ids:
                self._job_service.update(
                    job.id,
                    status=JobStatus.CANCELLED,
                    result={"reason": "user cancelled"},
                    clear_lease=True,
                    task_status="cancelled",
                )
                self._cancelled_job_ids.discard(job.id)
            else:
                self._job_service.update(
                    job.id,
                    status=JobStatus.QUEUED,
                    result={"reason": "worker stopped before completion"},
                    clear_lease=True,
                    task_status="queued",
                )
            raise
        except Exception as exc:
            self._job_service.update(
                job.id,
                status=JobStatus.FAILED,
                result={"error": str(exc)},
                clear_lease=True,
                task_status="failed",
            )
