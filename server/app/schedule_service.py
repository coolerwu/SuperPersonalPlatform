from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server.app.maintenance_service import MaintenanceService
from server.app.run_service import RunService
from server.app.system_log_service import SystemLogService
from server.app.webdav_context_service import WebDAVContextService
from server.infrastructure.config import Settings


SCHEDULE_STATUSES = {"idle", "running", "retrying", "completed", "failed", "disabled"}
BUILTIN_SCHEDULE_IDS = {"context_webdav_sync", "maintenance_cleanup"}
SCHEDULE_RETRY_MAX_ATTEMPTS = 3
SCHEDULE_RETRY_DELAY_SECONDS = 60
SCHEDULE_LOCK_HEARTBEAT_SECONDS = 15
SCHEDULE_LOCK_STALE_AFTER_SECONDS = 120
RHYTHMIC_DELIVERY_ID = "rhythmic_delivery"
RHYTHMIC_DELIVERY_DEFAULT_INTERVAL_SECONDS = 180
RHYTHMIC_DELIVERY_DEFAULT_MAX_ITEMS = 20


class ScheduleNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ScheduleTrigger:
    kind: str
    seconds: int = 0
    expr: str = ""
    timezone: str = "UTC"


@dataclass(frozen=True)
class ScheduleDefinition:
    id: str
    type: str
    enabled: bool
    trigger: ScheduleTrigger
    name: str = ""
    agent_id: str = ""
    prompt: str = ""
    context_ids: tuple[str, ...] = ()
    session_id: str = ""
    metadata: dict[str, Any] | None = None


class ScheduleService:
    poll_interval_seconds = 5

    def __init__(
        self,
        *,
        workspace: Path,
        settings: Settings,
        run_service: RunService,
        system_log_service: SystemLogService,
        maintenance_service: MaintenanceService | None = None,
        webdav_context_service: WebDAVContextService | None = None,
        channel_delivery_service: Any = None,
    ) -> None:
        self._workspace = workspace
        self._settings = settings
        self._run_service = run_service
        self._system_log_service = system_log_service
        self._maintenance_service = maintenance_service
        self._webdav_context_service = webdav_context_service
        self._channel_delivery_service = channel_delivery_service
        self._schedules_dir = workspace / "schedules"
        self._index_path = self._schedules_dir / "index.json"
        self._deliveries_dir = workspace / "deliveries"
        self._delivery_index_path = self._deliveries_dir / "index.json"

    async def run_forever(self, stop: asyncio.Event) -> None:
        self.bootstrap()
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_delivery_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.tick_delivery_queue()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        self.bootstrap()
        now = _now_dt()
        for definition in self._definitions():
            if not definition.enabled:
                self._set_disabled(definition)
                continue
            state = self._read_state(definition.id)
            if state.get("status") == "running":
                if not self._recover_stale_running(definition, state, now=now):
                    continue
                state = self._read_state(definition.id)
            raw_next_run_at = str(state.get("next_run_at") or "")
            if not raw_next_run_at:
                continue
            next_run_at = _parse_dt(raw_next_run_at) or now
            if next_run_at > now:
                continue
            await self._execute(definition, due_at=next_run_at)

    async def tick_delivery_queue(self) -> None:
        for definition in self._delivery_definitions():
            state = self._read_delivery_state(definition["id"])
            if state.get("status") in {"completed", "failed"}:
                continue
            next_run_at = _parse_dt(str(state.get("next_run_at") or "")) or _now_dt()
            if next_run_at > _now_dt():
                continue
            await self._execute_delivery(definition, state)

    def list_schedules(self) -> list[dict[str, Any]]:
        self.bootstrap()
        return [self._detail(definition, include_events=False) for definition in self._definitions()]

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        self.bootstrap()
        definition = self._definition_or_raise(schedule_id)
        return self._detail(definition, include_events=True)

    def create_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.bootstrap()
        definition = self._validated_user_definition(payload)
        if self._definition_path(definition.id).exists():
            raise ValueError("schedule already exists")
        self._write_definition(definition)
        self._write_initial_state(definition)
        self._append_event(definition.id, "created", {"message": "schedule created"})
        self._upsert_index(definition)
        return self.get_schedule(definition.id)

    def update_schedule(self, schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.bootstrap()
        if schedule_id in BUILTIN_SCHEDULE_IDS:
            raise ValueError("built-in schedule cannot be edited")
        if not self._definition_path(schedule_id).exists():
            raise ScheduleNotFoundError(schedule_id)
        next_payload = {**payload, "id": schedule_id}
        definition = self._validated_user_definition(next_payload)
        self._write_definition(definition)
        self._write_initial_state(definition)
        self._append_event(definition.id, "updated", {"message": "schedule updated"})
        self._upsert_index(definition)
        return self.get_schedule(definition.id)

    def delete_schedule(self, schedule_id: str) -> None:
        self.bootstrap()
        if schedule_id in BUILTIN_SCHEDULE_IDS:
            raise ValueError("built-in schedule cannot be deleted")
        if not self._definition_path(schedule_id).exists():
            raise ScheduleNotFoundError(schedule_id)
        shutil.rmtree(self._schedule_dir(schedule_id), ignore_errors=True)
        index = self._read_index()
        schedules = index.get("schedules") if isinstance(index, dict) else []
        if not isinstance(schedules, list):
            schedules = []
        self._write_index_from_summaries(
            [item for item in schedules if isinstance(item, dict) and item.get("id") != schedule_id]
        )

    async def run_now(self, schedule_id: str) -> dict[str, Any]:
        self.bootstrap()
        definition = self._definition_or_raise(schedule_id)
        await self._execute(definition, due_at=_now_dt())
        return self.get_schedule(schedule_id)

    def bootstrap(self) -> None:
        self._schedules_dir.mkdir(parents=True, exist_ok=True)
        schedules = self._read_index().get("schedules")
        if not isinstance(schedules, list):
            schedules = []
        known_ids = {str(item.get("id")) for item in schedules if isinstance(item, dict)}
        for definition in self._builtin_definitions():
            self._write_definition(definition)
            self._ensure_state(definition)
            if definition.id not in known_ids:
                schedules.append(self._summary(definition, self._read_state(definition.id)))
            else:
                schedules = [
                    self._summary(definition, self._read_state(definition.id))
                    if isinstance(item, dict) and item.get("id") == definition.id
                    else item
                    for item in schedules
                ]
        self._write_index_from_summaries(schedules)

    def _builtin_definitions(self) -> tuple[ScheduleDefinition, ...]:
        return (self._builtin_webdav_definition(), self._builtin_maintenance_definition())

    def _builtin_webdav_definition(self) -> ScheduleDefinition:
        return ScheduleDefinition(
            id="context_webdav_sync",
            type="webdav_sync",
            name="Context WebDAV 同步",
            enabled=(
                self._settings.nutstore.enabled
                and self._settings.context.webdav_sync.enabled
                and bool(self._settings.context.webdav_permissions)
            ),
            trigger=ScheduleTrigger(
                kind="interval",
                seconds=self._settings.context.webdav_sync.interval_seconds,
            ),
        )

    def _builtin_maintenance_definition(self) -> ScheduleDefinition:
        return ScheduleDefinition(
            id="maintenance_cleanup",
            type="maintenance_cleanup",
            name="维护清理",
            enabled=self._settings.maintenance.enabled,
            trigger=ScheduleTrigger(
                kind="interval",
                seconds=self._settings.maintenance.interval_seconds,
            ),
            metadata={
                "retention_days": self._settings.maintenance.retention_days,
                "dry_run": self._settings.maintenance.dry_run,
            },
        )

    async def _execute(self, definition: ScheduleDefinition, *, due_at: datetime) -> None:
        locked = self._try_lock(definition.id)
        if not locked:
            return
        started_at = _now()
        heartbeat_task: asyncio.Task[None] | None = None
        self._write_state(
            definition.id,
            {
                **self._read_state(definition.id),
                "status": "running",
                "started_at": started_at,
                "heartbeat_at": started_at,
                "current_run_id": "",
                "updated_at": started_at,
            },
        )
        self._append_event(definition.id, "running", {"message": "schedule started"})
        heartbeat_task = asyncio.create_task(self._heartbeat_running_schedule(definition.id))
        try:
            result = await self._execute_type(definition)
        except Exception as exc:  # noqa: BLE001
            completed_at = _now()
            completed_dt = _now_dt()
            previous_state = self._read_state(definition.id)
            retry_attempts = int(previous_state.get("retry_attempts") or 0) + 1
            retrying = retry_attempts <= SCHEDULE_RETRY_MAX_ATTEMPTS
            next_run_at = (
                (completed_dt + timedelta(seconds=SCHEDULE_RETRY_DELAY_SECONDS)).isoformat()
                if retrying
                else self._next_run_at(definition, due_at=due_at, completed_at=completed_dt)
            )
            error = {"type": exc.__class__.__name__, "message": str(exc)}
            current_run_id = str(previous_state.get("current_run_id") or "").strip()
            if current_run_id:
                self._run_service.fail_run(current_run_id, error=error)
            state = {
                **previous_state,
                "status": "retrying" if retrying else "failed",
                "last_status": "failed",
                "last_error": error,
                "last_run_at": completed_at,
                "next_run_at": next_run_at,
                "retry_attempts": retry_attempts,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "current_run_id": "",
                "updated_at": completed_at,
            }
            self._write_state(definition.id, state)
            self._append_event(
                definition.id,
                "retry_scheduled" if retrying else "failed",
                {
                    **error,
                    "retry_attempts": retry_attempts,
                    "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                    "next_run_at": next_run_at,
                },
            )
            self._system_log_service.append_line(
                f"schedule id={definition.id} type={definition.type} status={'retrying' if retrying else 'failed'} "
                f"retry={retry_attempts}/{SCHEDULE_RETRY_MAX_ATTEMPTS} error={exc}"
            )
        else:
            completed_at = _now()
            next_run_at = self._next_run_at(definition, due_at=due_at, completed_at=_now_dt())
            state = {
                **self._read_state(definition.id),
                "status": "completed",
                "last_status": "completed",
                "last_error": None,
                "last_run_at": completed_at,
                "next_run_at": next_run_at,
                "retry_attempts": 0,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "current_run_id": "",
                "updated_at": completed_at,
            }
            if isinstance(result, dict) and result.get("run_id"):
                state["last_run_id"] = result["run_id"]
            self._write_state(definition.id, state)
            self._append_event(definition.id, "completed", result if isinstance(result, dict) else {})
            self._system_log_service.append_line(f"schedule id={definition.id} type={definition.type} status=ok")
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            self._clear_lock(definition.id)
            self._upsert_index(definition)

    async def _execute_type(self, definition: ScheduleDefinition) -> dict[str, Any]:
        if definition.type == "webdav_sync":
            if self._webdav_context_service is None:
                raise RuntimeError("webdav context sync is not enabled")
            await self._webdav_context_service.refresh()
            return {"message": "webdav context synced"}
        if definition.type == "maintenance_cleanup":
            if self._maintenance_service is None:
                raise RuntimeError("maintenance cleanup is not enabled")
            report = await asyncio.to_thread(self._maintenance_service.cleanup)
            return {
                "message": "maintenance cleanup completed",
                "dry_run": report.get("dry_run", False),
                "retention_days": report.get("retention_days"),
                "summary": report.get("summary", {}),
                "items": len(report.get("items") or []),
            }
        if definition.type == "agent_run":
            if not definition.prompt.strip():
                raise RuntimeError("schedule prompt is required")
            run = await self._run_service.create_run(
                content=definition.prompt,
                agent_id=definition.agent_id,
                context_ids=definition.context_ids,
                source="schedule",
                session_id=definition.session_id,
                metadata={"schedule_id": definition.id, **(definition.metadata or {})},
            )
            run_id = str(run["run_id"])
            self._set_current_run(definition.id, run_id)
            completed = await self._run_service.execute_run(run_id)
            delivery = await self._deliver_agent_run_result(definition, completed)
            return {"message": "agent run completed", "run_id": run_id, "delivery": delivery}
        raise RuntimeError(f"unsupported schedule type: {definition.type}")

    async def _heartbeat_running_schedule(self, schedule_id: str) -> None:
        while True:
            await asyncio.sleep(SCHEDULE_LOCK_HEARTBEAT_SECONDS)
            heartbeat_at = _now()
            lock_path = self._lock_path(schedule_id)
            lock = _read_json(lock_path)
            if isinstance(lock, dict):
                lock["pid"] = os.getpid()
                lock["heartbeat_at"] = heartbeat_at
                _write_json(lock_path, lock)
            state = self._read_state(schedule_id)
            if state.get("status") == "running":
                state["heartbeat_at"] = heartbeat_at
                self._write_state(schedule_id, state)

    def _set_current_run(self, schedule_id: str, run_id: str) -> None:
        state = self._read_state(schedule_id)
        state["current_run_id"] = run_id
        state["updated_at"] = _now()
        self._write_state(schedule_id, state)

    def _recover_stale_running(
        self,
        definition: ScheduleDefinition,
        state: dict[str, Any],
        *,
        now: datetime,
    ) -> bool:
        lock_path = self._lock_path(definition.id)
        if lock_path.exists() and not _lock_is_stale(lock_path):
            return False
        completed_at = now.isoformat()
        due_at = _parse_dt(str(state.get("next_run_at") or "")) or now
        retry_attempts = int(state.get("retry_attempts") or 0) + 1
        retrying = retry_attempts <= SCHEDULE_RETRY_MAX_ATTEMPTS
        next_run_at = (
            (now + timedelta(seconds=SCHEDULE_RETRY_DELAY_SECONDS)).isoformat()
            if retrying
            else self._next_run_at(definition, due_at=due_at, completed_at=now)
        )
        error = {
            "type": "ScheduleStaleLockError",
            "message": "schedule was left running by a stale worker lock",
        }
        current_run_id = str(state.get("current_run_id") or "").strip() or self._run_service.latest_active_run_for_schedule(
            definition.id
        )
        if current_run_id:
            self._run_service.fail_run(current_run_id, error=error)
        self._clear_lock(definition.id)
        self._write_state(
            definition.id,
            {
                **state,
                "status": "retrying" if retrying else "failed",
                "last_status": "failed",
                "last_error": error,
                "last_run_at": completed_at,
                "next_run_at": next_run_at,
                "retry_attempts": retry_attempts,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "current_run_id": "",
                "updated_at": completed_at,
            },
        )
        self._append_event(
            definition.id,
            "retry_scheduled" if retrying else "failed",
            {
                **error,
                "retry_attempts": retry_attempts,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "next_run_at": next_run_at,
            },
        )
        self._upsert_index(definition)
        self._system_log_service.append_line(
            f"schedule id={definition.id} type={definition.type} status={'retrying' if retrying else 'failed'} "
            f"retry={retry_attempts}/{SCHEDULE_RETRY_MAX_ATTEMPTS} error=stale running lock"
        )
        return True

    async def _deliver_agent_run_result(
        self,
        definition: ScheduleDefinition,
        completed: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = definition.metadata or {}
        delivery = metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {}
        run_id = str(completed.get("run_id") or "")
        if not delivery:
            return {"status": "skipped", "reason": "no delivery target"}
        if delivery.get("channel") != "wechat":
            return {"status": "skipped", "reason": f"unsupported channel: {delivery.get('channel')}"}
        if self._channel_delivery_service is None:
            result = {"status": "failed", "error": "channel delivery service is not configured"}
            if run_id:
                self._run_service.set_delivery_status(run_id, "failed", extra={"delivery": delivery}, error=result)
            return result
        result_payload = completed.get("result") if isinstance(completed.get("result"), dict) else {}
        result_text = str(result_payload.get("content") or result_payload.get("error") or "定时任务没有返回内容")
        rhythmic_config = _rhythmic_delivery_config(definition, completed)
        if rhythmic_config is not None:
            queued = self._enqueue_rhythmic_delivery(
                definition,
                completed,
                delivery=delivery,
                text=result_text,
                interval_seconds=rhythmic_config["interval_seconds"],
                max_items=rhythmic_config["max_items"],
            )
            if run_id:
                self._run_service.set_delivery_status(run_id, "queued", extra={"delivery": delivery, "queue": queued})
            self._append_event(definition.id, "delivery_queued", queued)
            return queued
        try:
            await self._channel_delivery_service.deliver_text(
                channel="wechat",
                account_id=str(delivery.get("account_id") or ""),
                to_user_id=str(delivery.get("to_user_id") or delivery.get("peer_id") or ""),
                context_token=str(delivery.get("context_token") or ""),
                text=result_text,
            )
        except Exception as exc:  # noqa: BLE001
            error = {"type": exc.__class__.__name__, "message": str(exc)}
            if run_id:
                self._run_service.set_delivery_status(run_id, "failed", extra={"delivery": delivery}, error=error)
            self._append_event(definition.id, "delivery_failed", error)
            return {"status": "failed", "error": error}
        if run_id:
            self._run_service.set_delivery_status(run_id, "delivered", extra={"delivery": delivery})
        self._append_event(definition.id, "delivered", {"channel": "wechat", "run_id": run_id})
        return {"status": "delivered", "channel": "wechat"}

    def _enqueue_rhythmic_delivery(
        self,
        definition: ScheduleDefinition,
        completed: dict[str, Any],
        *,
        delivery: dict[str, Any],
        text: str,
        interval_seconds: int,
        max_items: int,
    ) -> dict[str, Any]:
        items = _extract_delivery_items(text, max_items=max_items)
        if not items:
            items = [text.strip() or "定时任务没有返回内容"]
        delivery_id = f"delivery_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = _now_dt()
        item_states = []
        definition_items = []
        for index, item_text in enumerate(items):
            scheduled_at = (now + timedelta(seconds=interval_seconds * index)).isoformat()
            definition_items.append({"index": index, "text": item_text, "scheduled_at": scheduled_at})
            item_states.append({"index": index, "status": "pending", "scheduled_at": scheduled_at})
        payload = {
            "schema_version": 1,
            "id": delivery_id,
            "source_schedule_id": definition.id,
            "source_run_id": str(completed.get("run_id") or ""),
            "agent_id": definition.agent_id,
            "session_id": definition.session_id,
            "middleware": {
                "id": RHYTHMIC_DELIVERY_ID,
                "interval_seconds": interval_seconds,
                "max_items": max_items,
            },
            "delivery": delivery,
            "items": definition_items,
            "created_at": now.isoformat(),
        }
        _write_json(self._delivery_definition_path(delivery_id), payload)
        self._write_delivery_state(
            delivery_id,
            {
                "schema_version": 1,
                "delivery_id": delivery_id,
                "status": "pending",
                "next_run_at": item_states[0]["scheduled_at"],
                "items": item_states,
                "sent_count": 0,
                "failed_count": 0,
                "updated_at": now.isoformat(),
            },
        )
        self._append_delivery_event(delivery_id, "created", {"items": len(items)})
        self._upsert_delivery_index(payload, self._read_delivery_state(delivery_id))
        return {
            "status": "queued",
            "channel": "wechat",
            "delivery_id": delivery_id,
            "items": len(items),
            "interval_seconds": interval_seconds,
        }

    async def _execute_delivery(self, definition: dict[str, Any], state: dict[str, Any]) -> None:
        delivery_id = str(definition.get("id") or "")
        if not delivery_id:
            return
        delivery = definition.get("delivery") if isinstance(definition.get("delivery"), dict) else {}
        if delivery.get("channel") != "wechat" or self._channel_delivery_service is None:
            state["status"] = "failed"
            state["failed_count"] = len(state.get("items") or [])
            state["updated_at"] = _now()
            self._write_delivery_state(delivery_id, state)
            self._append_delivery_event(delivery_id, "failed", {"reason": "delivery target unavailable"})
            self._upsert_delivery_index(definition, state)
            return
        items = definition.get("items") if isinstance(definition.get("items"), list) else []
        item_states = state.get("items") if isinstance(state.get("items"), list) else []
        item_state_by_index = {
            int(item.get("index") or 0): item for item in item_states if isinstance(item, dict)
        }
        now = _now_dt()
        for item in items:
            if not isinstance(item, dict):
                continue
            index = int(item.get("index") or 0)
            item_state = item_state_by_index.get(index)
            if not isinstance(item_state, dict) or item_state.get("status") != "pending":
                continue
            scheduled_at = _parse_dt(str(item_state.get("scheduled_at") or "")) or now
            if scheduled_at > now:
                continue
            item_state["status"] = "running"
            item_state["updated_at"] = _now()
            state["status"] = "running"
            state["updated_at"] = item_state["updated_at"]
            self._write_delivery_state(delivery_id, state)
            try:
                await self._channel_delivery_service.deliver_text(
                    channel="wechat",
                    account_id=str(delivery.get("account_id") or ""),
                    to_user_id=str(delivery.get("to_user_id") or delivery.get("peer_id") or ""),
                    context_token=str(delivery.get("context_token") or ""),
                    text=str(item.get("text") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                error = {"type": exc.__class__.__name__, "message": str(exc)}
                item_state["status"] = "failed"
                item_state["error"] = error
                item_state["updated_at"] = _now()
                state["failed_count"] = int(state.get("failed_count") or 0) + 1
                self._append_delivery_event(delivery_id, "item_failed", {"index": index, "error": error})
            else:
                item_state["status"] = "sent"
                item_state["sent_at"] = _now()
                item_state["updated_at"] = item_state["sent_at"]
                state["sent_count"] = int(state.get("sent_count") or 0) + 1
                self._append_delivery_event(delivery_id, "item_sent", {"index": index})

        pending_items = [item for item in item_states if isinstance(item, dict) and item.get("status") == "pending"]
        failed_count = int(state.get("failed_count") or 0)
        if pending_items:
            next_pending = min(
                (_parse_dt(str(item.get("scheduled_at") or "")) or now for item in pending_items),
                default=now,
            )
            state["status"] = "pending" if failed_count == 0 else "partial_failed"
            state["next_run_at"] = next_pending.isoformat()
        elif failed_count:
            state["status"] = "partial_failed" if int(state.get("sent_count") or 0) else "failed"
            state["next_run_at"] = ""
        else:
            state["status"] = "completed"
            state["next_run_at"] = ""
        latest_state = self._read_delivery_state(delivery_id)
        if isinstance(latest_state, dict) and "seq" in latest_state:
            state["seq"] = latest_state["seq"]
        state["updated_at"] = _now()
        self._write_delivery_state(delivery_id, state)
        self._upsert_delivery_index(definition, state)

    def _next_run_at(self, definition: ScheduleDefinition, *, due_at: datetime, completed_at: datetime) -> str:
        trigger = definition.trigger
        if trigger.kind == "once":
            return ""
        if trigger.kind == "interval":
            seconds = max(int(trigger.seconds or 0), 1)
            return (completed_at + timedelta(seconds=seconds)).isoformat()
        if trigger.kind == "cron":
            return _next_cron_time(trigger.expr, timezone_name=trigger.timezone, after=completed_at).isoformat()
        return (due_at + timedelta(days=1)).isoformat()

    def _definitions(self) -> list[ScheduleDefinition]:
        index = self._read_index()
        raw_schedules = index.get("schedules")
        if not isinstance(raw_schedules, list):
            return []
        definitions: list[ScheduleDefinition] = []
        for item in raw_schedules:
            if not isinstance(item, dict):
                continue
            schedule_id = str(item.get("id") or "").strip()
            if not _valid_schedule_id(schedule_id):
                continue
            raw = _read_json(self._definition_path(schedule_id))
            definition = _parse_definition(raw)
            if definition is not None:
                definitions.append(definition)
        return definitions

    def _ensure_state(self, definition: ScheduleDefinition) -> None:
        state_path = self._state_path(definition.id)
        if state_path.exists():
            return
        now = _now()
        status = "idle" if definition.enabled else "disabled"
        self._write_state(
            definition.id,
            {
                "schema_version": 1,
                "schedule_id": definition.id,
                "status": status,
                "next_run_at": now if definition.enabled else "",
                "last_run_at": "",
                "last_status": "",
                "last_error": None,
                "last_run_id": "",
                "retry_attempts": 0,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "updated_at": now,
            },
        )

    def _write_initial_state(self, definition: ScheduleDefinition) -> None:
        now = _now()
        existing = self._read_state(definition.id)
        self._write_state(
            definition.id,
            {
                "schema_version": 1,
                "schedule_id": definition.id,
                "status": "idle" if definition.enabled else "disabled",
                "next_run_at": self._initial_next_run_at(definition) if definition.enabled else "",
                "last_run_at": "",
                "last_status": "",
                "last_error": None,
                "last_run_id": "",
                "retry_attempts": 0,
                "retry_max_attempts": SCHEDULE_RETRY_MAX_ATTEMPTS,
                "seq": int(existing.get("seq") or 0),
                "updated_at": now,
            },
        )

    def _set_disabled(self, definition: ScheduleDefinition) -> None:
        state = self._read_state(definition.id)
        state["status"] = "disabled"
        state["next_run_at"] = ""
        state["updated_at"] = _now()
        self._write_state(definition.id, state)
        self._upsert_index(definition)

    def _try_lock(self, schedule_id: str) -> bool:
        lock_path = self._lock_path(schedule_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists() and _lock_is_stale(lock_path):
            lock_path.unlink(missing_ok=True)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            now = _now()
            json.dump(
                {"pid": os.getpid(), "created_at": now, "heartbeat_at": now},
                handle,
                ensure_ascii=False,
                sort_keys=True,
            )
        return True

    def _clear_lock(self, schedule_id: str) -> None:
        self._lock_path(schedule_id).unlink(missing_ok=True)

    def _append_event(self, schedule_id: str, event_type: str, payload: dict[str, Any]) -> None:
        schedule_dir = self._schedule_dir(schedule_id)
        schedule_dir.mkdir(parents=True, exist_ok=True)
        state = self._read_state(schedule_id)
        seq = int(state.get("seq") or 0) + 1
        state["seq"] = seq
        state["updated_at"] = _now()
        self._write_state(schedule_id, state)
        event = {
            "seq": seq,
            "schedule_id": schedule_id,
            "type": event_type,
            "created_at": state["updated_at"],
            "payload": payload,
        }
        with (schedule_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
            event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _upsert_index(self, definition: ScheduleDefinition) -> None:
        index = self._read_index()
        schedules = index.get("schedules") if isinstance(index, dict) else []
        if not isinstance(schedules, list):
            schedules = []
        summary = self._summary(definition, self._read_state(definition.id))
        next_schedules = [
            item for item in schedules if isinstance(item, dict) and item.get("id") != definition.id
        ]
        next_schedules.append(summary)
        self._write_index_from_summaries(next_schedules)

    def _summary(self, definition: ScheduleDefinition, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": definition.id,
            "name": definition.name or definition.id,
            "type": definition.type,
            "enabled": definition.enabled,
            "built_in": definition.id in BUILTIN_SCHEDULE_IDS,
            "agent_id": definition.agent_id,
            "prompt": definition.prompt,
            "trigger": _trigger_payload(definition.trigger),
            "status": state.get("status", "idle"),
            "next_run_at": state.get("next_run_at", ""),
            "last_run_at": state.get("last_run_at", ""),
            "last_status": state.get("last_status", ""),
            "last_error": state.get("last_error"),
            "last_run_id": state.get("last_run_id", ""),
            "retry_attempts": state.get("retry_attempts", 0),
            "retry_max_attempts": state.get("retry_max_attempts", SCHEDULE_RETRY_MAX_ATTEMPTS),
            "updated_at": state.get("updated_at", ""),
        }

    def _write_definition(self, definition: ScheduleDefinition) -> None:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "id": definition.id,
            "name": definition.name,
            "type": definition.type,
            "enabled": definition.enabled,
            "trigger": _trigger_payload(definition.trigger),
        }
        if definition.metadata:
            payload["metadata"] = definition.metadata
        if definition.type == "agent_run":
            payload.update(
                {
                    "agent_id": definition.agent_id,
                    "prompt": definition.prompt,
                    "context_ids": list(definition.context_ids),
                    "session_id": definition.session_id,
                    "metadata": definition.metadata or {},
                }
            )
        _write_json(self._definition_path(definition.id), payload)

    def _definition_or_raise(self, schedule_id: str) -> ScheduleDefinition:
        schedule_id = schedule_id.strip()
        if not _valid_schedule_id(schedule_id):
            raise ScheduleNotFoundError(schedule_id)
        raw = _read_json(self._definition_path(schedule_id))
        definition = _parse_definition(raw)
        if definition is None:
            raise ScheduleNotFoundError(schedule_id)
        return definition

    def _detail(self, definition: ScheduleDefinition, *, include_events: bool) -> dict[str, Any]:
        payload = {
            "definition": _definition_payload(definition),
            "state": self._read_state(definition.id),
            "summary": self._summary(definition, self._read_state(definition.id)),
        }
        if include_events:
            payload["events"] = self._read_events(definition.id)
        return payload

    def _read_events(self, schedule_id: str) -> list[dict[str, Any]]:
        events_path = self._schedule_dir(schedule_id) / "events.jsonl"
        if not events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events[-100:]

    def _validated_user_definition(self, payload: dict[str, Any]) -> ScheduleDefinition:
        raw = {**payload, "type": "agent_run"}
        definition = _parse_definition(raw)
        if definition is None:
            raise ValueError("schedule id is required")
        if definition.id in BUILTIN_SCHEDULE_IDS:
            raise ValueError("schedule id is reserved")
        if definition.type != "agent_run":
            raise ValueError("only agent_run schedules can be managed")
        if not definition.prompt.strip():
            raise ValueError("schedule prompt is required")
        if not definition.agent_id:
            raise ValueError("schedule agent_id is required")
        self._settings.agent_workspace.get_agent(definition.agent_id)
        _validate_trigger(definition.trigger)
        return definition

    def _initial_next_run_at(self, definition: ScheduleDefinition) -> str:
        now = _now_dt()
        trigger = definition.trigger
        if trigger.kind == "once":
            scheduled_at = _parse_dt(trigger.expr)
            if scheduled_at is None:
                raise ValueError("once trigger expr must be an ISO datetime")
            return (scheduled_at if scheduled_at > now else now).isoformat()
        if trigger.kind == "interval":
            return (now + timedelta(seconds=max(trigger.seconds, 60))).isoformat()
        if trigger.kind == "cron":
            return _next_cron_time(trigger.expr, timezone_name=trigger.timezone, after=now).isoformat()
        raise ValueError("unsupported trigger kind")

    def _read_state(self, schedule_id: str) -> dict[str, Any]:
        value = _read_json(self._state_path(schedule_id))
        if not isinstance(value, dict):
            return {}
        return value

    def _write_state(self, schedule_id: str, state: dict[str, Any]) -> None:
        _write_json(self._state_path(schedule_id), state)

    def _read_index(self) -> dict[str, Any]:
        value = _read_json(self._index_path)
        if not isinstance(value, dict):
            return {"schema_version": 1, "schedules": []}
        if "schedules" not in value:
            value["schedules"] = []
        return value

    def _write_index_from_summaries(self, summaries: list[Any]) -> None:
        schedules = [item for item in summaries if isinstance(item, dict) and _valid_schedule_id(str(item.get("id") or ""))]
        schedules.sort(key=lambda item: str(item.get("id") or ""))
        _write_json(self._index_path, {"schema_version": 1, "schedules": schedules})

    def _schedule_dir(self, schedule_id: str) -> Path:
        return self._schedules_dir / schedule_id

    def _definition_path(self, schedule_id: str) -> Path:
        return self._schedule_dir(schedule_id) / "definition.json"

    def _state_path(self, schedule_id: str) -> Path:
        return self._schedule_dir(schedule_id) / "state.json"

    def _lock_path(self, schedule_id: str) -> Path:
        return self._schedule_dir(schedule_id) / "lock.json"

    def _delivery_definitions(self) -> list[dict[str, Any]]:
        index = self._read_delivery_index()
        raw_deliveries = index.get("deliveries")
        if not isinstance(raw_deliveries, list):
            return []
        definitions: list[dict[str, Any]] = []
        for item in raw_deliveries:
            if not isinstance(item, dict):
                continue
            delivery_id = str(item.get("id") or "").strip()
            if not _valid_schedule_id(delivery_id):
                continue
            definition = _read_json(self._delivery_definition_path(delivery_id))
            if isinstance(definition, dict):
                definitions.append(definition)
        return definitions

    def _read_delivery_state(self, delivery_id: str) -> dict[str, Any]:
        value = _read_json(self._delivery_state_path(delivery_id))
        return value if isinstance(value, dict) else {}

    def _write_delivery_state(self, delivery_id: str, state: dict[str, Any]) -> None:
        _write_json(self._delivery_state_path(delivery_id), state)

    def _append_delivery_event(self, delivery_id: str, event_type: str, payload: dict[str, Any]) -> None:
        delivery_dir = self._delivery_dir(delivery_id)
        delivery_dir.mkdir(parents=True, exist_ok=True)
        state = self._read_delivery_state(delivery_id)
        seq = int(state.get("seq") or 0) + 1
        state["seq"] = seq
        state["updated_at"] = _now()
        self._write_delivery_state(delivery_id, state)
        event = {
            "seq": seq,
            "delivery_id": delivery_id,
            "type": event_type,
            "created_at": state["updated_at"],
            "payload": payload,
        }
        with (delivery_dir / "events.jsonl").open("a", encoding="utf-8") as event_file:
            event_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_delivery_index(self) -> dict[str, Any]:
        value = _read_json(self._delivery_index_path)
        if not isinstance(value, dict):
            return {"schema_version": 1, "deliveries": []}
        if "deliveries" not in value:
            value["deliveries"] = []
        return value

    def _upsert_delivery_index(self, definition: dict[str, Any], state: dict[str, Any]) -> None:
        summary = {
            "id": definition.get("id", ""),
            "source_schedule_id": definition.get("source_schedule_id", ""),
            "source_run_id": definition.get("source_run_id", ""),
            "agent_id": definition.get("agent_id", ""),
            "session_id": definition.get("session_id", ""),
            "middleware": definition.get("middleware", {}),
            "status": state.get("status", "pending"),
            "next_run_at": state.get("next_run_at", ""),
            "sent_count": state.get("sent_count", 0),
            "failed_count": state.get("failed_count", 0),
            "updated_at": state.get("updated_at", ""),
        }
        index = self._read_delivery_index()
        deliveries = index.get("deliveries") if isinstance(index, dict) else []
        if not isinstance(deliveries, list):
            deliveries = []
        next_deliveries = [item for item in deliveries if isinstance(item, dict) and item.get("id") != summary["id"]]
        next_deliveries.insert(0, summary)
        _write_json(self._delivery_index_path, {"schema_version": 1, "deliveries": next_deliveries})

    def _delivery_dir(self, delivery_id: str) -> Path:
        return self._deliveries_dir / delivery_id

    def _delivery_definition_path(self, delivery_id: str) -> Path:
        return self._delivery_dir(delivery_id) / "definition.json"

    def _delivery_state_path(self, delivery_id: str) -> Path:
        return self._delivery_dir(delivery_id) / "state.json"


def _parse_definition(raw: Any) -> ScheduleDefinition | None:
    if not isinstance(raw, dict):
        return None
    schedule_id = str(raw.get("id") or "").strip()
    schedule_name = str(raw.get("name") or schedule_id).strip()
    schedule_type = str(raw.get("type") or "").strip()
    trigger_raw = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else {}
    if not _valid_schedule_id(schedule_id) or not schedule_type:
        return None
    trigger = ScheduleTrigger(
        kind=str(trigger_raw.get("kind") or "").strip(),
        seconds=int(trigger_raw.get("seconds") or 0),
        expr=str(trigger_raw.get("expr") or "").strip(),
        timezone=str(trigger_raw.get("timezone") or "UTC").strip() or "UTC",
    )
    return ScheduleDefinition(
        id=schedule_id,
        type=schedule_type,
        enabled=bool(raw.get("enabled", True)),
        trigger=trigger,
        name=schedule_name,
        agent_id=str(raw.get("agent_id") or "").strip(),
        prompt=str(raw.get("prompt") or ""),
        context_ids=tuple(str(item).strip() for item in raw.get("context_ids") or [] if str(item).strip()),
        session_id=str(raw.get("session_id") or "").strip(),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    )


def _definition_payload(definition: ScheduleDefinition) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": definition.id,
        "name": definition.name or definition.id,
        "type": definition.type,
        "enabled": definition.enabled,
        "built_in": definition.id in BUILTIN_SCHEDULE_IDS,
        "trigger": _trigger_payload(definition.trigger),
        "agent_id": definition.agent_id,
        "prompt": definition.prompt,
        "context_ids": list(definition.context_ids),
        "session_id": definition.session_id,
        "metadata": definition.metadata or {},
    }


def _rhythmic_delivery_config(definition: ScheduleDefinition, completed: dict[str, Any]) -> dict[str, int] | None:
    config = _extract_rhythmic_delivery_config(definition.metadata or {})
    if config is None:
        agent = ((completed.get("input") or {}).get("snapshot") or {}).get("agent")
        deepagent = agent.get("deepagent") if isinstance(agent, dict) else {}
        config = _extract_rhythmic_delivery_config(deepagent if isinstance(deepagent, dict) else {})
    if config is None:
        return None
    return {
        "interval_seconds": max(int(config.get("interval_seconds") or RHYTHMIC_DELIVERY_DEFAULT_INTERVAL_SECONDS), 1),
        "max_items": max(int(config.get("max_items") or RHYTHMIC_DELIVERY_DEFAULT_MAX_ITEMS), 1),
    }


def _extract_rhythmic_delivery_config(raw: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        raw.get("delivery_middleware"),
        raw.get("middleware"),
        raw.get("middlewares"),
    ]
    delivery = raw.get("delivery") if isinstance(raw.get("delivery"), dict) else {}
    candidates.extend([delivery.get("middleware"), delivery.get("delivery_middleware")])
    for candidate in candidates:
        config = _candidate_rhythmic_delivery_config(candidate)
        if config is not None:
            return config
    return None


def _candidate_rhythmic_delivery_config(candidate: Any) -> dict[str, Any] | None:
    if isinstance(candidate, str):
        return {} if candidate.strip() == RHYTHMIC_DELIVERY_ID else None
    if isinstance(candidate, dict):
        if str(candidate.get("id") or candidate.get("name") or "").strip() == RHYTHMIC_DELIVERY_ID:
            return candidate
        return None
    if isinstance(candidate, list | tuple):
        for item in candidate:
            config = _candidate_rhythmic_delivery_config(item)
            if config is not None:
                return config
    return None


def _extract_delivery_items(text: str, *, max_items: int) -> list[str]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return []
    tagged = [_clean_delivery_item(match.group(1)) for match in _DELIVERY_ITEM_RE.finditer(raw_text)]
    tagged_items = [item for item in tagged if item]
    if tagged_items:
        return tagged_items[:max_items]
    json_items = _extract_json_delivery_items(raw_text)
    if json_items:
        return json_items[:max_items]
    markdown_items = _extract_markdown_delivery_items(raw_text)
    if len(markdown_items) > 1:
        return markdown_items[:max_items]
    return [raw_text[:8000]]


_DELIVERY_ITEM_RE = re.compile(r"<delivery-item>(.*?)</delivery-item>", re.IGNORECASE | re.DOTALL)


def _extract_json_delivery_items(text: str) -> list[str]:
    with suppress(json.JSONDecodeError):
        parsed = json.loads(text)
        raw_items: Any = parsed
        if isinstance(parsed, dict):
            for key in ("delivery_items", "items", "messages"):
                if isinstance(parsed.get(key), list):
                    raw_items = parsed[key]
                    break
        if isinstance(raw_items, list):
            items = []
            for item in raw_items:
                if isinstance(item, str):
                    items.append(_clean_delivery_item(item))
                elif isinstance(item, dict):
                    items.append(_clean_delivery_item(str(item.get("text") or item.get("content") or item.get("message") or "")))
            return [item for item in items if item]
    return []


def _extract_markdown_delivery_items(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    marker = re.compile(r"^\s*(?:#{1,6}\s+|\d+[.、]\s+|[-*]\s+)")
    for line in text.splitlines():
        if marker.match(line) and current:
            sections.append(_clean_delivery_item("\n".join(current)))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(_clean_delivery_item("\n".join(current)))
    return [item for item in sections if item]


def _clean_delivery_item(text: str) -> str:
    return str(text or "").strip().strip("-").strip()


def _trigger_payload(trigger: ScheduleTrigger) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": trigger.kind}
    if trigger.kind == "interval":
        payload["seconds"] = int(trigger.seconds or 0)
    if trigger.kind in {"cron", "once"}:
        payload["expr"] = trigger.expr
    if trigger.kind == "cron":
        payload["timezone"] = trigger.timezone or "UTC"
    return payload


def _validate_trigger(trigger: ScheduleTrigger) -> None:
    if trigger.kind == "interval":
        if int(trigger.seconds or 0) < 60:
            raise ValueError("interval trigger seconds must be at least 60")
        return
    if trigger.kind == "once":
        if _parse_dt(trigger.expr) is None:
            raise ValueError("once trigger expr must be an ISO datetime")
        return
    if trigger.kind == "cron":
        _next_cron_time(trigger.expr, timezone_name=trigger.timezone, after=_now_dt())
        return
    raise ValueError("unsupported trigger kind")


def _next_cron_time(expr: str, *, timezone_name: str, after: datetime) -> datetime:
    minute_values, hour_values, day_values, month_values, weekday_values = _parse_cron_expr(expr)
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    cursor = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = cursor + timedelta(days=366)
    while cursor <= deadline:
        if (
            cursor.minute in minute_values
            and cursor.hour in hour_values
            and cursor.day in day_values
            and cursor.month in month_values
            and cursor.weekday() in weekday_values
        ):
            return cursor.astimezone(timezone.utc)
        cursor += timedelta(minutes=1)
    raise ValueError("cron expression has no next time within one year")


def _parse_cron_expr(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise ValueError("cron trigger must use 5 fields")
    return (
        _parse_cron_field(fields[0], 0, 59),
        _parse_cron_field(fields[1], 0, 23),
        _parse_cron_field(fields[2], 1, 31),
        _parse_cron_field(fields[3], 1, 12),
        _normalize_weekdays(_parse_cron_field(fields[4], 0, 7)),
    )


def _parse_cron_field(raw: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if token.startswith("*/"):
            step = int(token[2:])
            if step < 1:
                raise ValueError("cron step must be positive")
            values.update(range(minimum, maximum + 1, step))
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            if start > end:
                raise ValueError("cron range start must be before end")
            values.update(range(start, end + 1))
            continue
        values.add(int(token))
    if not values or any(value < minimum or value > maximum for value in values):
        raise ValueError("cron field is out of range")
    return values


def _normalize_weekdays(values: set[int]) -> set[int]:
    normalized = set()
    for value in values:
        normalized.add(6 if value in {0, 7} else value - 1)
    return normalized


def _valid_schedule_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _lock_is_stale(path: Path) -> bool:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return True
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return True
    if pid < 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    heartbeat_at = _parse_dt(str(payload.get("heartbeat_at") or ""))
    if heartbeat_at is None:
        return False
    return _now_dt() - heartbeat_at > timedelta(seconds=SCHEDULE_LOCK_STALE_AFTER_SECONDS)


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
