import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.app.maintenance_service import MaintenanceService
from server.infrastructure.config import MaintenanceConfig, parse_settings


def test_maintenance_defaults_to_15_days() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "llm": {"models": []},
            "agents": {"definitions": []},
        }
    )

    assert settings.maintenance.retention_days == 15


def test_maintenance_preview_does_not_delete(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=16)
    _write_run(tmp_path, "run_old", "completed", old)

    service = MaintenanceService(tmp_path, MaintenanceConfig(retention_days=15))
    report = service.preview()

    assert report["dry_run"] is True
    assert report["summary"]["runs"] == 1
    assert (tmp_path / "runs" / "run_old").exists()
    assert _read_json(tmp_path / "runs" / "index.json")["runs"][0]["run_id"] == "run_old"


def test_maintenance_deletes_old_terminal_runs_but_keeps_active_runs(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=16)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    _write_run(tmp_path, "run_old_done", "completed", old)
    _write_run(tmp_path, "run_old_running", "running", old)
    _write_run(tmp_path, "run_recent_done", "completed", recent)

    report = MaintenanceService(tmp_path, MaintenanceConfig(retention_days=15)).cleanup(dry_run=False)

    assert report["summary"]["runs"] == 1
    assert not (tmp_path / "runs" / "run_old_done").exists()
    assert (tmp_path / "runs" / "run_old_running").exists()
    assert (tmp_path / "runs" / "run_recent_done").exists()
    assert [item["run_id"] for item in _read_json(tmp_path / "runs" / "index.json")["runs"]] == [
        "run_old_running",
        "run_recent_done",
    ]


def test_maintenance_deletes_inactive_sessions_except_active_run_references(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=16)
    _write_session(tmp_path, "session_old", old)
    _write_session(tmp_path, "session_protected", old)
    _write_run(tmp_path, "run_active", "running", old, session_id="session_protected")

    report = MaintenanceService(tmp_path, MaintenanceConfig(retention_days=15)).cleanup(dry_run=False)

    assert report["summary"]["sessions"] == 1
    assert not (tmp_path / "sessions" / "session_old").exists()
    assert (tmp_path / "sessions" / "session_protected").exists()
    assert [item["session_id"] for item in _read_json(tmp_path / "sessions" / "index.json")["sessions"]] == [
        "session_protected",
    ]


def test_maintenance_trims_schedule_events_logs_scratch_and_cache(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=16)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    schedule_dir = tmp_path / "schedules" / "context_webdav_sync"
    schedule_dir.mkdir(parents=True)
    _append_jsonl(schedule_dir / "events.jsonl", {"created_at": old.isoformat(), "type": "running"})
    _append_jsonl(schedule_dir / "events.jsonl", {"created_at": recent.isoformat(), "type": "completed"})
    old_log = tmp_path / "logs" / "platform-2026-01-01.log"
    old_log.parent.mkdir(parents=True)
    old_log.write_text("old log", encoding="utf-8")
    scratch_file = tmp_path / "agents" / "assistant" / "scratch" / "old.txt"
    cache_file = tmp_path / "context" / "state" / "cache" / "old.txt"
    scratch_file.parent.mkdir(parents=True)
    cache_file.parent.mkdir(parents=True)
    scratch_file.write_text("scratch", encoding="utf-8")
    cache_file.write_text("cache", encoding="utf-8")
    _set_mtime(scratch_file, old)
    _set_mtime(cache_file, old)

    report = MaintenanceService(tmp_path, MaintenanceConfig(retention_days=15)).cleanup(dry_run=False)

    assert report["summary"]["schedule_events"] == 1
    assert report["summary"]["logs"] == 1
    assert report["summary"]["agent_scratch"] == 1
    assert report["summary"]["context_cache"] == 1
    assert json.loads((schedule_dir / "events.jsonl").read_text(encoding="utf-8"))["type"] == "completed"
    assert not old_log.exists()
    assert not scratch_file.exists()
    assert not cache_file.exists()


def _write_run(
    workspace: Path,
    run_id: str,
    status: str,
    updated_at: datetime,
    *,
    session_id: str = "",
) -> None:
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "input.json",
        {
            "run_id": run_id,
            "source": "test",
            "agent_id": "assistant",
            "session_id": session_id,
            "created_at": updated_at.isoformat(),
        },
    )
    _write_json(run_dir / "state.json", {"run_id": run_id, "status": status, "updated_at": updated_at.isoformat()})
    _write_json(run_dir / "result.json", {"status": status})
    _write_json(run_dir / "lock.json", {"created_at": updated_at.isoformat()})
    index_path = workspace / "runs" / "index.json"
    index = _read_json(index_path) if index_path.exists() else {"schema_version": 1, "runs": []}
    index["runs"].append(
        {
            "run_id": run_id,
            "status": status,
            "agent_id": "assistant",
            "session_id": session_id,
            "created_at": updated_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        }
    )
    _write_json(index_path, index)


def _write_session(workspace: Path, session_id: str, updated_at: datetime) -> None:
    session_dir = workspace / "sessions" / session_id
    session_dir.mkdir(parents=True)
    _write_json(
        session_dir / "state.json",
        {"session_id": session_id, "status": "active", "updated_at": updated_at.isoformat()},
    )
    (session_dir / "messages.jsonl").write_text("", encoding="utf-8")
    index_path = workspace / "sessions" / "index.json"
    index = _read_json(index_path) if index_path.exists() else {"schema_version": 1, "sessions": []}
    index["sessions"].append({"session_id": session_id, "status": "active", "updated_at": updated_at.isoformat()})
    _write_json(index_path, index)


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _set_mtime(path: Path, value: datetime) -> None:
    timestamp = value.timestamp()
    os.utime(path, (timestamp, timestamp))
