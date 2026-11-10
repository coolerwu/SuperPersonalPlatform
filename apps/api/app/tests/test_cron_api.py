from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import CronJob
from app.services.identifiers import is_canonical_uuid
from app.services.settings_store import (
    AgentSection,
    ChannelSection,
    ConversationSection,
)


async def _set_agent_status(store, agent_id: str, status: str) -> None:
    settings = await store.get()
    agent = next(item for item in settings.agents if item.id == agent_id)
    await store.update_agent(agent.model_copy(update={"status": status}))


class FakeRegistry:
    def __init__(
        self, *, busy: bool = False, submit_error: Exception | None = None
    ) -> None:
        self.turns = []
        self.busy = busy
        self.submit_error = submit_error

    def is_busy(self, session_id: str) -> bool:
        return self.busy

    async def submit(self, turn):
        if self.submit_error is not None:
            raise self.submit_error
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


def test_manual_run_submit_failure_returns_503_and_consumes_run(client) -> None:
    client.app.state.registry = FakeRegistry(
        submit_error=RuntimeError("registry closed")
    )
    job = _create_job(client).json()
    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 503

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == job["id"])
    assert row["last_run_at"] is not None


async def test_push_target_uses_settings_conversation(client) -> None:
    store = client.app.state.settings_store
    await store.update_channel(
        ChannelSection(
            id="ch-api",
            agent_id="default",
            type="wechat",
            name="测试微信",
            status="active",
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


async def test_push_target_contract_uses_external_id(client) -> None:
    """The contacts endpoint must hand the cron page a bare external id.

    ``ChannelConversationOut.id`` is the compound settings key and is not a
    valid push target; the frontend and API contract is ``external_id``.
    """
    store = client.app.state.settings_store
    suffix = uuid.uuid4().hex[:8]
    channel_id = f"ch-contract-{suffix}"
    external_id = f"wx-contract-{suffix}"
    job_id = None
    await store.update_channel(
        ChannelSection(
            id=channel_id,
            agent_id="default",
            type="wechat",
            name="契约微信",
            status="active",
        )
    )
    await store.update_conversation(
        f"{channel_id}:{external_id}",
        ConversationSection(
            session_id=str(uuid.uuid4()),
            channel_id=channel_id,
            display_name="Alice",
        ),
    )
    try:
        rows = client.get(f"/api/channels/{channel_id}/conversations").json()
        row = next(item for item in rows if item["session_id"])
        assert row["id"] == f"{channel_id}:{external_id}"
        assert row["external_id"] == external_id

        created = _create_job(
            client,
            push_channel_id=channel_id,
            push_conversation_id=row["external_id"],
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        assert created.json()["push_conversation_id"] == external_id

        composite = _create_job(
            client,
            push_channel_id=channel_id,
            push_conversation_id=row["id"],
        )
        assert composite.status_code == 400
        assert "联系人" in composite.json()["detail"]

        assert client.delete(f"/api/channels/{channel_id}").status_code == 200
        async with SessionLocal() as db:
            job = await db.get(CronJob, job_id)
            assert job is not None
            assert job.push_channel_id is None
            assert job.push_conversation_id is None
    finally:
        if job_id is not None:
            client.delete(f"/api/cron-jobs/{job_id}")
        # Deleting twice is harmless: the first call above already removed it.
        client.delete(f"/api/channels/{channel_id}")


async def test_push_target_rejects_inactive_channel(client) -> None:
    store = client.app.state.settings_store
    suffix = uuid.uuid4().hex[:8]
    channel_id = f"ch-paused-{suffix}"
    external_id = f"wx-paused-{suffix}"
    await store.update_channel(
        ChannelSection(
            id=channel_id,
            agent_id="default",
            type="wechat",
            name="已暂停微信",
            status="paused",
        )
    )
    await store.update_conversation(
        f"{channel_id}:{external_id}",
        ConversationSection(
            session_id=str(uuid.uuid4()),
            channel_id=channel_id,
            display_name="Alice",
        ),
    )

    response = _create_job(
        client, push_channel_id=channel_id, push_conversation_id=external_id
    )
    assert response.status_code == 400
    assert "未启用" in response.json()["detail"]


async def test_update_push_target_sets_and_clears(client) -> None:
    store = client.app.state.settings_store
    suffix = uuid.uuid4().hex[:8]
    channel_id = f"ch-upd-{suffix}"
    external_id = f"wx-upd-{suffix}"
    await store.update_channel(
        ChannelSection(
            id=channel_id,
            agent_id="default",
            type="wechat",
            name="更新微信",
            status="active",
        )
    )
    await store.update_conversation(
        f"{channel_id}:{external_id}",
        ConversationSection(
            session_id=str(uuid.uuid4()),
            channel_id=channel_id,
            display_name="Bob",
        ),
    )

    job = _create_job(client).json()
    updated = client.put(
        f"/api/cron-jobs/{job['id']}",
        json={
            "push_channel_id": channel_id,
            "push_conversation_id": external_id,
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["push_channel_id"] == channel_id
    assert body["push_conversation_id"] == external_id
    assert body["push_channel_name"] == "更新微信"
    assert body["push_conversation_name"] == "Bob"

    partial = client.put(
        f"/api/cron-jobs/{job['id']}", json={"push_channel_id": channel_id}
    )
    assert partial.status_code == 400

    cleared = client.put(
        f"/api/cron-jobs/{job['id']}",
        json={"push_channel_id": "", "push_conversation_id": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["push_channel_id"] is None
    assert cleared.json()["push_conversation_id"] is None


async def test_paused_agent_can_view_edit_and_delete_jobs(client) -> None:
    client.app.state.registry = FakeRegistry()
    store = client.app.state.settings_store
    job = _create_job(client).json()
    await _set_agent_status(store, "default", "paused")
    try:
        created = _create_job(client)
        assert created.status_code == 400
        assert "Agent 已暂停" in created.json()["detail"]

        listed = client.get("/api/cron-jobs?agent_id=default")
        assert listed.status_code == 200
        assert any(item["id"] == job["id"] for item in listed.json())

        updated = client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": False})
        assert updated.status_code == 200

        run = client.post(f"/api/cron-jobs/{job['id']}/run")
        assert run.status_code == 400
        assert "Agent 已暂停" in run.json()["detail"]

        assert client.delete(f"/api/cron-jobs/{job['id']}").status_code == 200
    finally:
        await _set_agent_status(store, "default", "active")


async def test_archived_agent_jobs_are_hidden(client) -> None:
    store = client.app.state.settings_store
    job = _create_job(client).json()
    await _set_agent_status(store, "default", "archived")
    try:
        assert _create_job(client).status_code == 404
        assert client.get("/api/cron-jobs?agent_id=default").status_code == 404
        assert client.post(f"/api/cron-jobs/{job['id']}/run").status_code == 404
        assert (
            client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": False}).status_code
            == 404
        )
        assert client.delete(f"/api/cron-jobs/{job['id']}").status_code == 404
    finally:
        await _set_agent_status(store, "default", "active")


async def test_permanent_delete_uses_cron_session_for_busy_and_cleanup(
    client,
) -> None:
    store = client.app.state.settings_store
    suffix = uuid.uuid4().hex[:8]
    agent = client.post(
        "/api/agents", json={"name": f"CronBusy-{suffix}"}
    ).json()
    agent_id = agent["id"]
    job = _create_job(client, agent_id=agent_id).json()
    session_id = job["session_id"]

    class RecordingRegistry:
        def __init__(self, busy: bool) -> None:
            self.busy = busy
            self.closed: list[str] = []

        def is_busy(self, candidate: str) -> bool:
            return self.busy and candidate == session_id

        async def close_session(self, candidate: str) -> None:
            self.closed.append(candidate)

    registry = RecordingRegistry(busy=True)
    client.app.state.registry = registry
    try:
        assert client.delete(f"/api/agents/{agent_id}/permanent").status_code == 409
        assert registry.closed == []

        registry.busy = False
        assert client.delete(f"/api/agents/{agent_id}/permanent").status_code == 200
        assert session_id in registry.closed
    finally:
        client.app.state.registry = None
        client.delete(f"/api/agents/{agent_id}/permanent")


async def test_permanent_agent_delete_cascades_cron_jobs(client) -> None:
    store = client.app.state.settings_store
    suffix = uuid.uuid4().hex[:8]
    agent = client.post(
        "/api/agents", json={"name": f"CronAgent-{suffix}"}
    ).json()
    agent_id = agent["id"]
    await store.update_agent(
        AgentSection(id=agent_id, name=f"CronAgent-{suffix}", status="active")
    )
    try:
        job = _create_job(client, agent_id=agent_id).json()
        assert client.delete(f"/api/agents/{agent_id}/permanent").status_code == 200
        async with SessionLocal() as db:
            assert await db.get(CronJob, job["id"]) is None
    finally:
        await store.mutate_raw(
            lambda data: data.update(
                {
                    "agents": [
                        item
                        for item in data.get("agents", [])
                        if item.get("id") != agent_id
                    ]
                }
            )
        )
