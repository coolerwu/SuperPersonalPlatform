from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import suppress
from typing import Any

from server.app.run_service import RUN_WORKER_POLL_SECONDS, RunService
from server.app.system_log_service import SystemLogService


class RunWorkerService:
    """Persistent run executor backed by workspace/runs on disk."""

    def __init__(
        self,
        *,
        run_service: RunService,
        system_log_service: SystemLogService,
        worker_id: str | None = None,
        poll_interval_seconds: float = RUN_WORKER_POLL_SECONDS,
    ) -> None:
        self._run_service = run_service
        self._system_log_service = system_log_service
        self._worker_id = worker_id or f"run-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._poll_interval_seconds = poll_interval_seconds
        self._wake_event = asyncio.Event()
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def wake(self) -> None:
        self._wake_event.set()

    async def run_forever(self, stop: asyncio.Event) -> None:
        self._run_service.reconcile_incomplete_runs()
        while not stop.is_set():
            await self.tick()
            self._wake_event.clear()
            try:
                await asyncio.wait_for(_wait_for_any(stop, self._wake_event), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        if self._active_tasks:
            return
        claim = self._run_service.claim_next_run(worker_id=self._worker_id)
        if claim is None:
            return
        run_id = claim["run_id"]
        lease_id = claim["lease_id"]
        task = asyncio.create_task(self._execute_claimed(run_id, lease_id))
        self._active_tasks[run_id] = task
        task.add_done_callback(lambda _: self._active_tasks.pop(run_id, None))

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        cancelled = self._run_service.cancel_run(run_id)
        task = self._active_tasks.get(run_id)
        if task is not None:
            task.cancel()
        self.wake()
        return cancelled

    async def wait_for_run(self, run_id: str) -> dict[str, Any]:
        self.wake()
        while True:
            await self.tick()
            run = self._run_service.get_run(run_id)
            state = run.get("state") if isinstance(run.get("state"), dict) else {}
            if state.get("status") in {"completed", "failed", "cancelled"}:
                return run
            await asyncio.sleep(self._poll_interval_seconds)

    async def stop_active(self) -> None:
        for task in list(self._active_tasks.values()):
            task.cancel()
        for task in list(self._active_tasks.values()):
            with suppress(asyncio.CancelledError):
                await task

    async def _execute_claimed(self, run_id: str, lease_id: str) -> None:
        try:
            await self._run_service.execute_run(run_id, lease_id=lease_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._system_log_service.append_line(f"run worker {self._worker_id} failed run {run_id}: {exc}")
        finally:
            self.wake()


async def _wait_for_any(*events: asyncio.Event) -> None:
    tasks = [asyncio.create_task(event.wait()) for event in events]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
