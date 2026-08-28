import asyncio
import json
from datetime import datetime, timedelta, timezone

from server.app.schedule_service import ScheduleService
from server.app.system_log_service import SystemLogService
from server.infrastructure.config import parse_settings


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
nutstore:
  enabled: true
  username: user
  password: pass
context:
  webdav_sync:
    enabled: true
    root_path: /notebook
    interval_seconds: 600
  webdav_permissions:
    - path: /
      readable: true
      writable: false
      protected: true
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
"""


class FakeWebDAVContextService:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self) -> None:
        self.calls += 1


class FakeMaintenanceService:
    def __init__(self) -> None:
        self.calls = 0

    def cleanup(self):
        self.calls += 1
        return {
            "dry_run": False,
            "retention_days": 15,
            "summary": {"runs": 1, "sessions": 0, "bytes": 128},
            "items": [{"type": "runs"}],
        }


class FakeRunService:
    def __init__(self) -> None:
        self.created = []
        self.executed = []
        self.delivery_statuses = []
        self.failed_runs = []

    async def create_run(self, **kwargs):
        self.created.append(kwargs)
        return {"run_id": "run_test"}

    async def execute_run(self, run_id: str):
        self.executed.append(run_id)
        return {"run_id": run_id, "state": {"status": "completed"}, "result": {"content": "定时任务结果"}}

    def set_delivery_status(self, run_id: str, status: str, *, extra=None, error=None) -> None:
        self.delivery_statuses.append({"run_id": run_id, "status": status, "extra": extra, "error": error})

    def fail_run(self, run_id: str, *, error: dict) -> None:
        self.failed_runs.append({"run_id": run_id, "error": error})


class FlakyRunService(FakeRunService):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def execute_run(self, run_id: str):
        self.executed.append(run_id)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TimeoutError("browser timed out")
        return {"run_id": run_id, "state": {"status": "completed"}, "result": {"content": "ok"}}


class FakeChannelDeliveryService:
    def __init__(self) -> None:
        self.deliveries = []

    async def deliver_text(self, **kwargs):
        self.deliveries.append(kwargs)
        return {"ok": True}


def test_schedule_service_bootstraps_and_runs_webdav_sync(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    fake_webdav = FakeWebDAVContextService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=FakeRunService(),
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=fake_webdav,
    )

    asyncio.run(service.tick())

    assert fake_webdav.calls == 1
    definition = _read_json(tmp_path / "schedules" / "context_webdav_sync" / "definition.json")
    state = _read_json(tmp_path / "schedules" / "context_webdav_sync" / "state.json")
    index = _read_json(tmp_path / "schedules" / "index.json")
    assert definition["type"] == "webdav_sync"
    assert definition["trigger"]["seconds"] == 600
    assert state["status"] == "completed"
    assert state["next_run_at"]
    assert index["schedules"][0]["id"] == "context_webdav_sync"
    assert any(item["id"] == "maintenance_cleanup" for item in index["schedules"])


def test_schedule_service_bootstraps_and_runs_maintenance_cleanup(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    fake_maintenance = FakeMaintenanceService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=FakeRunService(),
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=fake_maintenance,
        webdav_context_service=FakeWebDAVContextService(),
    )

    asyncio.run(service.tick())

    definition = _read_json(tmp_path / "schedules" / "maintenance_cleanup" / "definition.json")
    state = _read_json(tmp_path / "schedules" / "maintenance_cleanup" / "state.json")
    events = (tmp_path / "schedules" / "maintenance_cleanup" / "events.jsonl").read_text(encoding="utf-8")
    assert fake_maintenance.calls == 1
    assert definition["type"] == "maintenance_cleanup"
    assert definition["trigger"]["seconds"] == 86400
    assert definition["metadata"] == {"dry_run": False, "retention_days": 15}
    assert state["status"] == "completed"
    assert "maintenance cleanup completed" in events


def test_schedule_service_runs_agent_schedule_from_files(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    run_service = FakeRunService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=run_service,
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=FakeWebDAVContextService(),
    )
    service.bootstrap()
    due_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    schedule_dir = tmp_path / "schedules" / "daily_notes_review"
    schedule_dir.mkdir(parents=True)
    _write_json(
        schedule_dir / "definition.json",
        {
            "schema_version": 1,
            "id": "daily_notes_review",
            "type": "agent_run",
            "enabled": True,
            "trigger": {"kind": "interval", "seconds": 3600},
            "agent_id": "assistant",
            "prompt": "总结最近笔记",
        },
    )
    _write_json(
        schedule_dir / "state.json",
        {
            "schema_version": 1,
            "schedule_id": "daily_notes_review",
            "status": "idle",
            "next_run_at": due_at,
        },
    )
    index = _read_json(tmp_path / "schedules" / "index.json")
    index["schedules"].append({"id": "daily_notes_review", "type": "agent_run", "enabled": True})
    _write_json(tmp_path / "schedules" / "index.json", index)

    asyncio.run(service.tick())

    assert run_service.created[0]["content"] == "总结最近笔记"
    assert run_service.created[0]["source"] == "schedule"
    assert run_service.created[0]["metadata"] == {"schedule_id": "daily_notes_review"}
    assert run_service.executed == ["run_test"]
    state = _read_json(schedule_dir / "state.json")
    assert state["status"] == "completed"
    assert state["last_run_id"] == "run_test"


def test_schedule_service_retries_failed_agent_schedule(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    run_service = FlakyRunService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=run_service,
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=FakeWebDAVContextService(),
    )
    service.bootstrap()
    due_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    schedule_dir = tmp_path / "schedules" / "retry_browser"
    schedule_dir.mkdir(parents=True)
    _write_json(
        schedule_dir / "definition.json",
        {
            "schema_version": 1,
            "id": "retry_browser",
            "type": "agent_run",
            "enabled": True,
            "trigger": {"kind": "interval", "seconds": 3600},
            "agent_id": "assistant",
            "prompt": "抓取 HN",
        },
    )
    _write_json(
        schedule_dir / "state.json",
        {
            "schema_version": 1,
            "schedule_id": "retry_browser",
            "status": "idle",
            "next_run_at": due_at,
        },
    )
    index = _read_json(tmp_path / "schedules" / "index.json")
    index["schedules"].append({"id": "retry_browser", "type": "agent_run", "enabled": True})
    _write_json(tmp_path / "schedules" / "index.json", index)

    asyncio.run(service.tick())

    state = _read_json(schedule_dir / "state.json")
    assert state["status"] == "retrying"
    assert state["retry_attempts"] == 1
    assert state["retry_max_attempts"] == 3
    assert _parse_time(state["next_run_at"]) <= datetime.now(timezone.utc) + timedelta(seconds=65)
    events = (schedule_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "retry_scheduled" in events

    state["next_run_at"] = due_at
    _write_json(schedule_dir / "state.json", state)
    asyncio.run(service.tick())

    state = _read_json(schedule_dir / "state.json")
    assert state["status"] == "completed"
    assert state["retry_attempts"] == 0
    assert run_service.executed == ["run_test", "run_test"]


def test_schedule_service_recovers_stale_running_schedule(tmp_path, monkeypatch) -> None:
    settings = parse_settings(_raw_config())
    run_service = FakeRunService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=run_service,
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=FakeWebDAVContextService(),
    )
    service.bootstrap()
    due_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    schedule_dir = tmp_path / "schedules" / "stale_browser"
    schedule_dir.mkdir(parents=True)
    _write_json(
        schedule_dir / "definition.json",
        {
            "schema_version": 1,
            "id": "stale_browser",
            "type": "agent_run",
            "enabled": True,
            "trigger": {"kind": "interval", "seconds": 3600},
            "agent_id": "assistant",
            "prompt": "抓取网页",
        },
    )
    _write_json(
        schedule_dir / "state.json",
        {
            "schema_version": 1,
            "schedule_id": "stale_browser",
            "status": "running",
            "next_run_at": due_at,
            "current_run_id": "run_stale",
            "retry_attempts": 0,
        },
    )
    _write_json(schedule_dir / "lock.json", {"pid": 999999, "created_at": due_at})
    index = _read_json(tmp_path / "schedules" / "index.json")
    index["schedules"].append({"id": "stale_browser", "type": "agent_run", "enabled": True})
    _write_json(tmp_path / "schedules" / "index.json", index)

    def fake_kill(pid, signal):
        raise ProcessLookupError(pid)

    monkeypatch.setattr("server.app.schedule_service.os.kill", fake_kill)

    asyncio.run(service.tick())

    state = _read_json(schedule_dir / "state.json")
    assert state["status"] == "retrying"
    assert state["current_run_id"] == ""
    assert state["retry_attempts"] == 1
    assert not (schedule_dir / "lock.json").exists()
    assert run_service.failed_runs[0]["run_id"] == "run_stale"
    assert run_service.executed == []
    events = (schedule_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "ScheduleStaleLockError" in events


def test_schedule_service_delivers_agent_schedule_result_to_wechat(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    run_service = FakeRunService()
    delivery_service = FakeChannelDeliveryService()
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=run_service,
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=FakeWebDAVContextService(),
        channel_delivery_service=delivery_service,
    )
    service.bootstrap()
    due_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    schedule_dir = tmp_path / "schedules" / "agent_tool_reminder"
    schedule_dir.mkdir(parents=True)
    _write_json(
        schedule_dir / "definition.json",
        {
            "schema_version": 1,
            "id": "agent_tool_reminder",
            "type": "agent_run",
            "enabled": True,
            "trigger": {"kind": "once", "expr": due_at},
            "agent_id": "assistant",
            "prompt": "提醒我复盘",
            "session_id": "wechat_default_private_wxid",
            "metadata": {
                "created_by": {
                    "type": "agent_tool",
                    "agent_id": "assistant",
                    "session_id": "wechat_default_private_wxid",
                },
                "delivery": {
                    "channel": "wechat",
                    "account_id": "default",
                    "to_user_id": "wxid",
                    "context_token": "reply-token",
                },
            },
        },
    )
    _write_json(
        schedule_dir / "state.json",
        {
            "schema_version": 1,
            "schedule_id": "agent_tool_reminder",
            "status": "idle",
            "next_run_at": due_at,
        },
    )
    index = _read_json(tmp_path / "schedules" / "index.json")
    index["schedules"].append({"id": "agent_tool_reminder", "type": "agent_run", "enabled": True})
    _write_json(tmp_path / "schedules" / "index.json", index)

    asyncio.run(service.tick())

    assert delivery_service.deliveries == [
        {
            "channel": "wechat",
            "account_id": "default",
            "to_user_id": "wxid",
            "context_token": "reply-token",
            "text": "定时任务结果",
        }
    ]
    assert run_service.delivery_statuses[-1]["status"] == "delivered"
    events = (schedule_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "delivered" in events


def test_schedule_service_manages_user_schedules(tmp_path) -> None:
    settings = parse_settings(_raw_config())
    service = ScheduleService(
        workspace=tmp_path,
        settings=settings,
        run_service=FakeRunService(),
        system_log_service=SystemLogService(tmp_path),
        maintenance_service=FakeMaintenanceService(),
        webdav_context_service=FakeWebDAVContextService(),
    )

    created = service.create_schedule(
        {
            "id": "daily_review",
            "name": "Daily Review",
            "enabled": True,
            "trigger": {"kind": "interval", "seconds": 3600},
            "agent_id": "assistant",
            "prompt": "总结最近笔记",
            "context_ids": ["default"],
            "metadata": {"source": "test"},
        }
    )
    assert created["definition"]["id"] == "daily_review"
    assert created["definition"]["trigger"]["seconds"] == 3600

    updated = service.update_schedule(
        "daily_review",
        {
            "id": "ignored",
            "name": "Daily Review",
            "enabled": False,
            "trigger": {"kind": "interval", "seconds": 7200},
            "agent_id": "assistant",
            "prompt": "总结最近笔记",
        },
    )
    assert updated["definition"]["id"] == "daily_review"
    assert updated["definition"]["enabled"] is False
    assert updated["state"]["status"] == "disabled"

    service.delete_schedule("daily_review")

    assert not (tmp_path / "schedules" / "daily_review").exists()


def _raw_config() -> dict:
    import yaml

    return yaml.safe_load(CONFIG)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
