from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from server.infrastructure.config import MaintenanceConfig


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


class MaintenanceService:
    def __init__(self, workspace: Path, config: MaintenanceConfig) -> None:
        self._workspace = workspace
        self._config = config

    def preview(self) -> dict[str, Any]:
        return self.cleanup(dry_run=True)

    def cleanup(self, *, dry_run: bool | None = None) -> dict[str, Any]:
        effective_dry_run = self._config.dry_run if dry_run is None else dry_run
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._config.retention_days)
        report = _empty_report(
            dry_run=effective_dry_run,
            retention_days=self._config.retention_days,
            cutoff=cutoff,
        )
        protected_session_ids = self._protected_session_ids(cutoff)
        self._clean_runs(cutoff, report, dry_run=effective_dry_run)
        self._clean_sessions(cutoff, protected_session_ids, report, dry_run=effective_dry_run)
        self._trim_schedule_events(cutoff, report, dry_run=effective_dry_run)
        self._clean_logs(cutoff, report, dry_run=effective_dry_run)
        self._clean_agent_scratch(cutoff, report, dry_run=effective_dry_run)
        self._clean_context_cache(cutoff, report, dry_run=effective_dry_run)
        self._clean_stale_locks(cutoff, report, dry_run=effective_dry_run)
        return report

    def _protected_session_ids(self, cutoff: datetime) -> set[str]:
        protected: set[str] = set()
        for item in _read_index_items(self._workspace / "runs" / "index.json", "runs"):
            session_id = str(item.get("session_id") or "").strip()
            if not session_id:
                continue
            status = str(item.get("status") or "").strip()
            updated_at = _parse_dt(str(item.get("updated_at") or item.get("created_at") or ""))
            if status not in TERMINAL_RUN_STATUSES or (updated_at is not None and updated_at >= cutoff):
                protected.add(session_id)
        return protected

    def _clean_runs(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        index_path = self._workspace / "runs" / "index.json"
        original = _read_index(index_path, "runs")
        runs = original.get("runs") if isinstance(original, dict) else []
        if not isinstance(runs, list):
            runs = []
        kept: list[Any] = []
        changed = False
        for item in runs:
            if not isinstance(item, dict):
                changed = True
                continue
            run_id = str(item.get("run_id") or "").strip()
            status = str(item.get("status") or "").strip()
            updated_at = _parse_dt(str(item.get("updated_at") or item.get("created_at") or ""))
            if not run_id or "/" in run_id or "\\" in run_id:
                changed = True
                continue
            if status in TERMINAL_RUN_STATUSES and _is_older_than(updated_at, cutoff):
                path = self._workspace / "runs" / run_id
                size = _path_size(path)
                _add_item(report, "runs", path, "terminal run older than retention", size)
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                changed = True
                continue
            kept.append(item)
        if changed and not dry_run:
            _write_json(index_path, {"schema_version": 1, "runs": kept})

    def _clean_sessions(
        self,
        cutoff: datetime,
        protected_session_ids: set[str],
        report: dict[str, Any],
        *,
        dry_run: bool,
    ) -> None:
        index_path = self._workspace / "sessions" / "index.json"
        original = _read_index(index_path, "sessions")
        sessions = original.get("sessions") if isinstance(original, dict) else []
        if not isinstance(sessions, list):
            sessions = []
        kept: list[Any] = []
        changed = False
        for item in sessions:
            if not isinstance(item, dict):
                changed = True
                continue
            session_id = str(item.get("session_id") or "").strip()
            updated_at = _parse_dt(str(item.get("updated_at") or ""))
            if not session_id or "/" in session_id or "\\" in session_id:
                changed = True
                continue
            if session_id not in protected_session_ids and _is_older_than(updated_at, cutoff):
                path = self._workspace / "sessions" / session_id
                size = _path_size(path)
                _add_item(report, "sessions", path, "inactive session older than retention", size)
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                changed = True
                continue
            kept.append(item)
        if changed and not dry_run:
            _write_json(index_path, {"schema_version": 1, "sessions": kept})

    def _trim_schedule_events(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        schedules_dir = self._workspace / "schedules"
        if not schedules_dir.exists():
            return
        for events_path in sorted(schedules_dir.glob("*/events.jsonl")):
            kept: list[str] = []
            removed = 0
            removed_bytes = 0
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                created_at = _parse_dt(str(item.get("created_at") or ""))
                if _is_older_than(created_at, cutoff):
                    removed += 1
                    removed_bytes += len(line.encode("utf-8")) + 1
                else:
                    kept.append(line)
            if removed:
                _add_item(report, "schedule_events", events_path, f"trim {removed} old schedule events", removed_bytes)
                if not dry_run:
                    events_path.write_text(("\n".join(kept) + ("\n" if kept else "")), encoding="utf-8")

    def _clean_logs(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        logs_dir = self._workspace / "logs"
        if not logs_dir.exists():
            return
        cutoff_date = cutoff.date()
        for path in sorted(logs_dir.glob("platform-*.log")):
            log_date = _parse_log_date(path.name)
            if log_date is None or log_date >= cutoff_date:
                continue
            size = _path_size(path)
            _add_item(report, "logs", path, "platform log older than retention", size)
            if not dry_run:
                path.unlink(missing_ok=True)

    def _clean_agent_scratch(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        agents_dir = self._workspace / "agents"
        if not agents_dir.exists():
            return
        for scratch_dir in sorted(agents_dir.glob("*/scratch")):
            self._clean_files_under(scratch_dir, cutoff, report, bucket="agent_scratch", dry_run=dry_run)

    def _clean_context_cache(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        cache_dir = self._workspace / "context" / "state" / "cache"
        self._clean_files_under(cache_dir, cutoff, report, bucket="context_cache", dry_run=dry_run)

    def _clean_files_under(
        self,
        root: Path,
        cutoff: datetime,
        report: dict[str, Any],
        *,
        bucket: str,
        dry_run: bool,
    ) -> None:
        if not root.exists():
            return
        old_files = [path for path in sorted(root.rglob("*")) if path.is_file() and _mtime_older_than(path, cutoff)]
        for path in old_files:
            size = _path_size(path)
            _add_item(report, bucket, path, "file older than retention", size)
            if not dry_run:
                path.unlink(missing_ok=True)
        if not dry_run:
            _remove_empty_dirs(root)

    def _clean_stale_locks(self, cutoff: datetime, report: dict[str, Any], *, dry_run: bool) -> None:
        candidates: list[Path] = []
        candidates.extend((self._workspace / "runs").glob("*/lock.json"))
        candidates.extend((self._workspace / "schedules").glob("*/lock.json"))
        candidates.append(self._workspace / "logs" / "update-service.lock")
        for path in candidates:
            if not path.exists() or not _mtime_older_than(path, cutoff):
                continue
            size = _path_size(path)
            _add_item(report, "locks", path, "stale lock older than retention", size)
            if not dry_run:
                path.unlink(missing_ok=True)


def _empty_report(*, dry_run: bool, retention_days: int, cutoff: datetime) -> dict[str, Any]:
    return {
        "ok": True,
        "dry_run": dry_run,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "summary": {
            "runs": 0,
            "sessions": 0,
            "schedule_events": 0,
            "logs": 0,
            "agent_scratch": 0,
            "context_cache": 0,
            "locks": 0,
            "bytes": 0,
        },
        "items": [],
    }


def _add_item(report: dict[str, Any], bucket: str, path: Path, reason: str, size: int) -> None:
    report["summary"][bucket] = int(report["summary"].get(bucket) or 0) + 1
    report["summary"]["bytes"] = int(report["summary"].get("bytes") or 0) + size
    report["items"].append(
        {
            "type": bucket,
            "path": path.as_posix(),
            "reason": reason,
            "bytes": size,
        }
    )


def _read_index_items(path: Path, key: str) -> list[dict[str, Any]]:
    payload = _read_index(path, key)
    items = payload.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _read_index(path: Path, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, key: []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": 1, key: []}
    return payload if isinstance(payload, dict) else {"schema_version": 1, key: []}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_older_than(value: datetime | None, cutoff: datetime) -> bool:
    return value is not None and value < cutoff


def _mtime_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff
    except FileNotFoundError:
        return False


def _parse_log_date(filename: str):
    raw = filename.removeprefix("platform-").removesuffix(".log")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    empty_dirs = (item for item in root.rglob("*") if item.is_dir())
    for path in sorted(empty_dirs, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
