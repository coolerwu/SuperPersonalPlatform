from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.identifiers import is_canonical_uuid
from app.services.settings_store import ChannelSection, ConversationSection


class FakeRegistry:
    def __init__(self, *, busy: bool = False) -> None:
        self.turns = []
        self.busy = busy

    def is_busy(self, session_id: str) -> bool:
        return self.busy

    async def submit(self, turn):
        self.turns.append(turn)
        return turn.id


def _name(prefix: str = "任务") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_job(client, **overrides):
    payload = {
        "agent_id": "default",
        "name": _name(),
        "prompt": "总结昨天的工作区变化",
        "schedule": "0 8 * * *",
        "timezone": "Asia/Shanghai",
        "enabled": True,
    }
    payload.update(overrides)
    return client.post("/api/cron-jobs", json=payload)


def _future(value: str | None) -> bool:
    assert value is not None
    return datetime.fromisoformat(value) > datetime.now(timezone.utc)


def test_create_cron_job_returns_uuid_session(client) -> None:
    response = _create_job(client)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["enabled"] is True
    assert is_canonical_uuid(job["session_id"])
    assert "last_run_id" not in job
    assert "last_run_status" not in job
    assert _future(job["next_run_at"])

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == job["id"])
    assert row["session_id"] == job["session_id"]


def test_cron_validation_and_duplicate_name(client) -> None:
    name = _name("重复")
    assert _create_job(client, name=name).status_code == 201
    assert _create_job(client, name=name).status_code == 409
    assert _create_job(client, name="").status_code == 400
    assert _create_job(client, prompt="").status_code == 400
    assert _create_job(client, schedule="0 0 * * * *").status_code == 400
    assert _create_job(client, schedule="not a cron").status_code == 400
    assert _create_job(client, schedule="0 0 31 2 *").status_code == 400
    assert _create_job(client, schedule="0 0 31 2 *", enabled=False).status_code == 400
    assert _create_job(client, timezone="Mars/Olympus").status_code == 400


def test_pause_resume_edit_and_rename(client) -> None:
    job = _create_job(client).json()
    renamed = client.put(
        f"/api/cron-jobs/{job['id']}",
        json={"name": _name("改名"), "schedule": "*/15 * * * *"},
    )
    assert renamed.status_code == 200, renamed.text
    updated = renamed.json()
    assert updated["next_run_at"] is not None
    assert updated["session_id"] == job["session_id"]

    paused = client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": False})
    assert paused.status_code == 200
    assert paused.json()["next_run_at"] is None

    resumed = client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": True})
    assert resumed.status_code == 200
    assert _future(resumed.json()["next_run_at"])


def test_delete_job_removes_configuration(client) -> None:
    job = _create_job(client).json()
    deleted = client.delete(f"/api/cron-jobs/{job['id']}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["id"] == job["id"]
    assert body["session_id"] == job["session_id"]
    listed = client.get("/api/cron-jobs?agent_id=default").json()
    assert all(item["id"] != job["id"] for item in listed)
    assert client.delete(f"/api/cron-jobs/{job['id']}").status_code == 404


def test_manual_run_submits_cron_turn(client) -> None:
    registry = FakeRegistry()
    client.app.state.registry = registry
    job = _create_job(client).json()

    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["session_id"] == job["session_id"]
    assert body["last_run_at"] is not None

    assert len(registry.turns) == 1
    turn = registry.turns[0]
    assert turn.source == "cron"
    assert turn.agent_id == "default"
    assert turn.session_id == job["session_id"]
    assert turn.prompt == job["prompt"]
    assert turn.delivery is None


def test_manual_run_conflicts_when_session_busy(client) -> None:
    registry = FakeRegistry(busy=True)
    client.app.state.registry = registry
    job = _create_job(client).json()

    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "busy"
    assert "active_run_id" not in detail
    assert registry.turns == []


def test_manual_run_requires_runtime(client) -> None:
    job = _create_job(client).json()
    client.app.state.registry = None
    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 503


async def test_push_target_uses_settings_conversation(client) -> None:
    store = client.app.state.settings_store
    await store.update_channel(
        ChannelSection(
            id="ch-api", agent_id="default", type="wechat", name="测试微信"
        )
    )
    await store.update_conversation(
        "ch-api:wx-api",
        ConversationSection(
            session_id=str(uuid.uuid4()),
            channel_id="ch-api",
            display_name="Alice",
        ),
    )

    response = _create_job(
        client, push_channel_id="ch-api", push_conversation_id="wx-api"
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["push_channel_id"] == "ch-api"
    assert job["push_conversation_id"] == "wx-api"
    assert job["push_channel_name"] == "测试微信"
    assert job["push_conversation_name"] == "Alice"

    missing = _create_job(
        client, push_channel_id="ch-api", push_conversation_id="wx-missing"
    )
    assert missing.status_code == 400

    partial = _create_job(client, push_channel_id="ch-api")
    assert partial.status_code == 400
