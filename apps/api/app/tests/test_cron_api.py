from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Agent, CronJob, Run, Session


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


def test_create_cron_job_creates_dedicated_session(client) -> None:
    response = _create_job(client)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["enabled"] is True
    assert job["session_id"]
    assert _future(job["next_run_at"])

    sessions = client.get("/api/agents/default/sessions").json()
    session = next(item for item in sessions if item["id"] == job["session_id"])
    assert session["origin"] == "cron"
    assert session["title"] == f"定时 · {job['name']}"

    listed = client.get("/api/cron-jobs?agent_id=default").json()
    assert any(item["id"] == job["id"] for item in listed)


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

    sessions = client.get("/api/agents/default/sessions").json()
    session = next(item for item in sessions if item["id"] == updated["session_id"])
    assert session["title"] == f"定时 · {updated['name']}"

    paused = client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": False})
    assert paused.status_code == 200
    assert paused.json()["next_run_at"] is None

    resumed = client.put(f"/api/cron-jobs/{job['id']}", json={"enabled": True})
    assert resumed.status_code == 200
    assert _future(resumed.json()["next_run_at"])


def test_manual_run_creates_cron_run_and_conflicts(client) -> None:
    job = _create_job(client, name=_name("立即")).json()
    response = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["source"] == "cron"
    assert run["status"] == "queued"
    assert run["input"] == job["prompt"]
    assert run["session_id"] == job["session_id"]

    conflict = client.post(f"/api/cron-jobs/{job['id']}/run")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["active_run_id"] == run_id

    client.post(f"/api/runs/{run_id}/cancel")


def test_delete_job_keeps_session_and_delete_session_detaches_job(client) -> None:
    job = _create_job(client, name=_name("保留会话")).json()
    deleted = client.delete(f"/api/cron-jobs/{job['id']}")
    assert deleted.status_code == 200

    sessions = client.get("/api/agents/default/sessions").json()
    assert any(item["id"] == job["session_id"] for item in sessions)

    detached_job = _create_job(client, name=_name("解绑")).json()
    assert client.delete(f"/api/sessions/{detached_job['session_id']}").status_code == 200
    listed = client.get("/api/cron-jobs?agent_id=default").json()
    row = next(item for item in listed if item["id"] == detached_job["id"])
    assert row["session_id"] is None
    assert row["last_run_id"] is None


async def test_permanent_agent_delete_cascades_cron_jobs(client) -> None:
    agent = client.post("/api/agents", json={"name": f"CronAgent-{uuid.uuid4().hex[:6]}"}).json()
    job = _create_job(client, agent_id=agent["id"], name=_name("级联")).json()
    assert client.delete(f"/api/agents/{agent['id']}/permanent").status_code == 200
    async with SessionLocal() as db:
        assert await db.get(CronJob, job["id"]) is None
        assert await db.get(Session, job["session_id"]) is None


async def test_archived_agent_is_not_scheduled(client) -> None:
    from app.services.cron_service import run_due_jobs

    agent = client.post("/api/agents", json={"name": f"CronArchive-{uuid.uuid4().hex[:6]}"}).json()
    job = _create_job(client, agent_id=agent["id"], name=_name("归档")).json()

    async with SessionLocal() as db:
        stored_agent = await db.get(Agent, agent["id"])
        assert stored_agent is not None
        stored_agent.status = "archived"
        row = await db.get(CronJob, job["id"])
        assert row is not None
        row.next_run_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await db.commit()

    fired = await run_due_jobs()
    async with SessionLocal() as db:
        row = await db.get(CronJob, job["id"])
        assert row is not None
        assert row.last_run_at is None
        assert row.next_run_at is not None
        runs = (await db.scalars(select(Run).where(Run.session_id == row.session_id))).all()
        assert runs == []
    assert job["id"] not in fired

    assert client.delete(f"/api/agents/{agent['id']}/permanent").status_code == 200
