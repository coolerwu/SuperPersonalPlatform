from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from claude_agent_sdk.types import TERMINAL_TASK_STATUSES
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import SessionLocal
from app.models import RunningSession

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 10 * 60
CLEANUP_INTERVAL_SECONDS = 60.0
MAX_TASKS = 32
MAX_TASKS_JSON_BYTES = 64 * 1024
TERMINAL_STATUSES = frozenset(TERMINAL_TASK_STATUSES) | {"interrupted"}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_tasks(raw: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        logger.warning("ignoring malformed tasks_json", exc_info=True)
        return {}
    if not isinstance(data, list):
        logger.warning("ignoring non-list tasks_json")
        return {}
    tasks: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if task_id:
            tasks[task_id] = dict(item)
    return tasks


def _dump_tasks(tasks: dict[str, dict[str, Any]]) -> str:
    return json.dumps(
        list(tasks.values()), ensure_ascii=False, separators=(",", ":")
    )


def _is_terminal(task: dict[str, Any]) -> bool:
    return str(task.get("status") or "running") in TERMINAL_STATUSES


def _task_time(task: dict[str, Any], key: str) -> datetime | None:
    return _parse_iso(task.get(key))


class SessionTaskService:
    """Session-level task snapshot plus live event hub.

    Task state is authoritative in memory for active sessions and persisted to
    ``running_sessions.tasks_json`` with a short debounce. Subscribers receive
    live events; reconnecting clients get a fresh snapshot from the API.
    """

    def __init__(
        self,
        *,
        retention_seconds: int = RETENTION_SECONDS,
        cleanup_interval_seconds: float = CLEANUP_INTERVAL_SECONDS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.retention = timedelta(seconds=retention_seconds)
        self.cleanup_interval = cleanup_interval_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._live: dict[str, dict[str, dict[str, Any]]] = {}
        self._subscribers: dict[
            str, set[asyncio.Queue[dict[str, Any] | None]]
        ] = {}
        self._dirty: set[str] = set()
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._writer: asyncio.Task[None] | None = None
        self._cleaner: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._stop.clear()
        if self._writer is None:
            self._writer = asyncio.create_task(
                self._writer_loop(), name="session-task-writer"
            )
        if self._cleaner is None:
            self._cleaner = asyncio.create_task(
                self._cleaner_loop(), name="session-task-cleaner"
            )

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for task in (self._writer, self._cleaner):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._writer = None
        self._cleaner = None
        async with self._lock:
            for session_id, tasks in list(self._live.items()):
                if not tasks:
                    continue
                try:
                    await self._persist(session_id, tasks)
                except Exception:
                    logger.exception(
                        "session task flush failed session=%s", session_id
                    )
        for queues in list(self._subscribers.values()):
            for queue in list(queues):
                queue.put_nowait(None)

    async def startup(self) -> None:
        """Mark tasks left running by the previous process as interrupted."""
        async with self._lock:
            async with SessionLocal() as db:
                rows = list(await db.scalars(select(RunningSession)))
                now = self._now()
                now_iso = _iso(now)
                for row in rows:
                    try:
                        tasks = _load_tasks(row.tasks_json)
                        changed = False
                        for task in tasks.values():
                            if _is_terminal(task):
                                continue
                            task["status"] = "interrupted"
                            task["summary"] = "服务重启，任务已中断"
                            task["updated_at"] = now_iso
                            task["completed_at"] = now_iso
                            changed = True
                        if changed:
                            row.tasks_json = _dump_tasks(tasks)
                            row.updated_at = now
                    except Exception:
                        logger.exception(
                            "session task startup cleanup failed session=%s",
                            row.session_id,
                        )
                await db.commit()
            self._live.clear()

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        task_id = str(event.get("task_id") or "")
        kind = str(event.get("type") or "")
        if not task_id or kind not in (
            "task_started",
            "task_progress",
            "task_updated",
            "task_notification",
        ):
            return
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            now = self._now()
            now_iso = _iso(now)
            task = tasks.get(task_id)
            if task is None:
                task = {
                    "task_id": task_id,
                    "tool_use_id": event.get("tool_use_id"),
                    "description": "",
                    "status": "running",
                    "summary": "",
                    "last_tool_name": "",
                    "usage": None,
                    "started_at": now_iso,
                    "updated_at": now_iso,
                    "completed_at": None,
                }
                tasks[task_id] = task
            if kind == "task_progress" and _is_terminal(task):
                return
            if kind == "task_started":
                task["tool_use_id"] = event.get("tool_use_id") or task.get(
                    "tool_use_id"
                )
                task["description"] = str(
                    event.get("description") or task.get("description") or ""
                )
                task["status"] = "running"
                task["completed_at"] = None
                task["summary"] = ""
                task.setdefault("started_at", now_iso)
            elif kind == "task_progress":
                task["description"] = str(
                    event.get("description") or task.get("description") or ""
                )
                task["last_tool_name"] = str(
                    event.get("last_tool_name")
                    or task.get("last_tool_name")
                    or ""
                )
                if event.get("usage") is not None:
                    task["usage"] = event["usage"]
            elif kind == "task_updated":
                status = event.get("status")
                if status:
                    if (
                        _is_terminal(task)
                        and str(status) not in TERMINAL_STATUSES
                    ):
                        return
                    task["status"] = str(status)
                    if str(status) in TERMINAL_STATUSES:
                        task["completed_at"] = task.get("completed_at") or now_iso
            else:
                status = event.get("status")
                if status:
                    task["status"] = str(status)
                task["summary"] = str(
                    event.get("summary") or task.get("summary") or ""
                )
                if event.get("usage") is not None:
                    task["usage"] = event["usage"]
                if _is_terminal(task):
                    task["completed_at"] = task.get("completed_at") or now_iso
            task["updated_at"] = now_iso
            self._broadcast(session_id, event)
            self._trim(session_id, tasks)
            if kind == "task_progress":
                self._dirty.add(session_id)
                self._wake.set()
            else:
                await self._persist_safely(session_id, tasks)

    async def snapshot(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            tasks = self._live.get(session_id)
            async with SessionLocal() as db:
                row = await db.get(RunningSession, session_id)
            if tasks is None:
                tasks = _load_tasks(row.tasks_json) if row is not None else {}
                self._live[session_id] = tasks
            cutoff = self._now() - self.retention
            visible: list[dict[str, Any]] = []
            for task in tasks.values():
                if _is_terminal(task):
                    completed = _task_time(task, "completed_at") or _task_time(
                        task, "updated_at"
                    )
                    if completed is not None and completed < cutoff:
                        continue
                visible.append(dict(task))
            return {
                "has_turn": bool(row.has_turn) if row is not None else False,
                "updated_at": _iso(row.updated_at)
                if row is not None and row.updated_at
                else None,
                "tasks": visible,
            }

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(
        self, session_id: str, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        subscribers = self._subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    async def mark_interrupted(self, session_id: str, reason: str) -> None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            now_iso = _iso(self._now())
            changed = False
            for task in tasks.values():
                if _is_terminal(task):
                    continue
                task["status"] = "interrupted"
                task["summary"] = reason
                task["updated_at"] = now_iso
                task["completed_at"] = now_iso
                changed = True
                self._broadcast(
                    session_id,
                    {
                        "type": "task_notification",
                        "task_id": task["task_id"],
                        "status": "interrupted",
                        "summary": reason,
                        "usage": task.get("usage"),
                    },
                )
            if changed:
                await self._persist_safely(session_id, tasks)

    async def keep_alive_until(self, session_id: str) -> datetime | None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                tasks = await self._row_tasks(session_id)
                self._live[session_id] = tasks
            deadlines: list[datetime] = []
            for task in tasks.values():
                if _is_terminal(task):
                    continue
                started = _task_time(task, "started_at") or _task_time(
                    task, "updated_at"
                )
                if started is not None:
                    deadlines.append(started + self.retention)
            return max(deadlines) if deadlines else None

    async def flush(self, session_id: str) -> None:
        async with self._lock:
            tasks = self._live.get(session_id)
            if tasks is None:
                return
            await self._persist_safely(session_id, tasks)

    async def forget(self, session_id: str) -> None:
        async with self._lock:
            self._live.pop(session_id, None)
            self._dirty.discard(session_id)
            for queue in self._subscribers.pop(session_id, set()):
                queue.put_nowait(None)
            async with SessionLocal() as db:
                row = await db.get(RunningSession, session_id)
                if row is None:
                    return
                if row.has_turn:
                    logger.warning(
                        "refusing to forget active turn lease session=%s",
                        session_id,
                    )
                    return
                await db.delete(row)
                await db.commit()

    async def cleanup_once(self) -> None:
        async with self._lock:
            now = self._now()
            now_iso = _iso(now)
            async with SessionLocal() as db:
                rows = list(await db.scalars(select(RunningSession)))
                for row in rows:
                    try:
                        tasks = self._live.get(row.session_id)
                        if tasks is None:
                            tasks = _load_tasks(row.tasks_json)
                            self._live[row.session_id] = tasks
                        changed = False
                        removed: list[str] = []
                        for task_id, task in list(tasks.items()):
                            if _is_terminal(task):
                                completed = _task_time(
                                    task, "completed_at"
                                ) or _task_time(task, "updated_at")
                                if (
                                    completed is None
                                    or now - completed > self.retention
                                ):
                                    tasks.pop(task_id, None)
                                    removed.append(task_id)
                                    changed = True
                            else:
                                started = _task_time(
                                    task, "started_at"
                                ) or _task_time(task, "updated_at")
                                if (
                                    started is not None
                                    and now - started > self.retention
                                ):
                                    task["status"] = "interrupted"
                                    task["summary"] = "后台任务超过保活上限"
                                    task["updated_at"] = now_iso
                                    task["completed_at"] = now_iso
                                    changed = True
                                    self._broadcast(
                                        row.session_id,
                                        {
                                            "type": "task_notification",
                                            "task_id": task_id,
                                            "status": "interrupted",
                                            "summary": "后台任务超过保活上限",
                                            "usage": task.get("usage"),
                                        },
                                    )
                        if changed:
                            row.tasks_json = _dump_tasks(tasks)
                            row.updated_at = now
                        for task_id in removed:
                            self._broadcast(
                                row.session_id,
                                {"type": "task_removed", "task_id": task_id},
                            )
                        if not row.has_turn and not tasks:
                            await db.execute(
                                delete(RunningSession).where(
                                    RunningSession.session_id == row.session_id,
                                    RunningSession.has_turn.is_(False),
                                )
                            )
                            self._live.pop(row.session_id, None)
                    except Exception:
                        logger.exception(
                            "session task cleanup failed session=%s",
                            row.session_id,
                        )
                await db.commit()
            row_ids = {row.session_id for row in rows}
            for key in list(self._live):
                if key not in row_ids and not self._subscribers.get(key):
                    self._live.pop(key, None)

    async def _row_tasks(self, session_id: str) -> dict[str, dict[str, Any]]:
        async with SessionLocal() as db:
            row = await db.get(RunningSession, session_id)
        return _load_tasks(row.tasks_json) if row is not None else {}

    async def _persist(
        self, session_id: str, tasks: dict[str, dict[str, Any]]
    ) -> None:
        raw = _dump_tasks(tasks)
        now = self._now()
        async with SessionLocal() as db:
            stmt = (
                sqlite_insert(RunningSession)
                .values(
                    session_id=session_id,
                    has_turn=False,
                    tasks_json=raw,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[RunningSession.session_id],
                    set_={"tasks_json": raw, "updated_at": now},
                )
            )
            await db.execute(stmt)
            await db.commit()

    async def _persist_safely(
        self, session_id: str, tasks: dict[str, dict[str, Any]]
    ) -> None:
        try:
            await self._persist(session_id, tasks)
            self._dirty.discard(session_id)
        except Exception:
            logger.exception(
                "session task write failed session=%s", session_id
            )
            self._dirty.add(session_id)
            self._wake.set()

    def _broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(session_id, ())):
            queue.put_nowait(event)

    def _trim(
        self, session_id: str, tasks: dict[str, dict[str, Any]]
    ) -> None:
        removed: list[str] = []

        def drop(limit: int | None) -> None:
            terminal = sorted(
                (task for task in tasks.values() if _is_terminal(task)),
                key=lambda task: str(
                    task.get("completed_at") or task.get("updated_at") or ""
                ),
            )
            for task in terminal:
                if limit is not None and len(tasks) <= limit:
                    break
                if limit is None and len(_dump_tasks(tasks).encode("utf-8")) <= MAX_TASKS_JSON_BYTES:
                    break
                tasks.pop(str(task.get("task_id") or ""), None)
                removed.append(str(task.get("task_id") or ""))

        if len(tasks) > MAX_TASKS:
            drop(MAX_TASKS)
        if len(_dump_tasks(tasks).encode("utf-8")) > MAX_TASKS_JSON_BYTES:
            drop(None)
        for task_id in removed:
            self._broadcast(
                session_id, {"type": "task_removed", "task_id": task_id}
            )

    async def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            async with self._lock:
                dirty = list(self._dirty)
                self._dirty.clear()
                for session_id in dirty:
                    tasks = self._live.get(session_id)
                    if tasks is None:
                        continue
                    try:
                        await self._persist(session_id, tasks)
                    except Exception:
                        logger.exception(
                            "session task write failed session=%s", session_id
                        )
                        self._dirty.add(session_id)

    async def _cleaner_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session task cleanup failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.cleanup_interval
                )
            except asyncio.TimeoutError:
                pass
