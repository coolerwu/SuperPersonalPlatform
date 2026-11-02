from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CronJob, Run, Session
from app.services.cron_tools import (  # noqa: F401 - import registers the tools
    register_cron_tools,
)
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext

CRON_TOOL_NAMES = (
    "cron_list",
    "cron_create",
    "cron_update",
    "cron_delete",
    "cron_run",
)


def _name(prefix: str = "tool") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _tools(agent_id: str = "default") -> dict:
    context = SkillToolContext(api_key="", base_url="", agent_id=agent_id)
    return {
        tool.name: tool
        for tool in SKILL_TOOL_REGISTRY.resolve_names(CRON_TOOL_NAMES, context)
    }


def _job_id(output: str) -> str:
    marker = "id: "
    start = output.index(marker) + len(marker)
    return output[start:].split("）", 1)[0].strip()


def test_registry_has_cron_tools() -> None:
    register_cron_tools()
    for name in CRON_TOOL_NAMES:
        assert SKILL_TOOL_REGISTRY.has(name)


async def test_create_list_update_delete_via_tools(client) -> None:
    tools = _tools()
    assert set(tools) == set(CRON_TOOL_NAMES)

    name = _name()
    created = await tools["cron_create"].ainvoke(
        {
            "name": name,
            "prompt": "总结昨天的工作区变化",
            "schedule": "0 8 * * *",
            "timezone": "Asia/Shanghai",
        }
    )
    assert "已创建" in created
    job_id = _job_id(created)

    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.name == name
        assert job.enabled is True
        assert job.next_run_at is not None
        assert job.next_run_at > datetime.now(timezone.utc)
        session = await db.get(Session, job.session_id)
        assert session is not None
        assert session.origin == "cron"
        assert session.title == f"定时 · {name}"

    listed = await tools["cron_list"].ainvoke({})
    assert name in listed
    assert job_id in listed

    await tools["cron_update"].ainvoke({"job_id": job_id, "timezone": ""})
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.timezone == "Asia/Shanghai"

    paused = await tools["cron_update"].ainvoke({"job_id": job_id, "enabled": False})
    assert "暂停" in paused
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.enabled is False
        assert job.next_run_at is None

    resumed = await tools["cron_update"].ainvoke(
        {"job_id": job_id, "schedule": "*/15 * * * *", "enabled": True}
    )
    assert "启用" in resumed
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.schedule == "*/15 * * * *"
        assert job.next_run_at is not None
        session_id = job.session_id

    deleted = await tools["cron_delete"].ainvoke({"job_id": job_id})
    assert "已删除" in deleted
    async with SessionLocal() as db:
        assert await db.get(CronJob, job_id) is None
        assert await db.get(Session, session_id) is not None


async def test_tool_validation_and_duplicate_name(client) -> None:
    tools = _tools()
    name = _name("dup")
    payload = {
        "name": name,
        "prompt": "执行任务",
        "schedule": "0 8 * * *",
    }
    assert "已创建" in await tools["cron_create"].ainvoke(payload)
    duplicate = await tools["cron_create"].ainvoke(payload)
    assert duplicate.startswith("Error:")
    assert "同名" in duplicate

    assert (await tools["cron_create"].ainvoke({**payload, "name": ""})).startswith("Error:")
    assert (await tools["cron_create"].ainvoke({**payload, "prompt": ""})).startswith("Error:")
    assert (
        await tools["cron_create"].ainvoke({**payload, "schedule": "0 0 * * * *"})
    ).startswith("Error:")
    assert (
        await tools["cron_create"].ainvoke({**payload, "timezone": "Mars/Olympus"})
    ).startswith("Error:")


async def test_tools_are_scoped_to_current_agent(client) -> None:
    other = client.post("/api/agents", json={"name": f"CronTools-{uuid.uuid4().hex[:6]}"}).json()
    other_tools = _tools(other["id"])
    name = _name("other")
    created = await other_tools["cron_create"].ainvoke(
        {"name": name, "prompt": "只在另一个 Agent 运行", "schedule": "0 9 * * *"}
    )
    job_id = _job_id(created)

    listed = await _tools()["cron_list"].ainvoke({})
    assert name not in listed

    assert (
        await _tools()["cron_update"].ainvoke({"job_id": job_id, "name": "hijack"})
    ).startswith("Error:")
    assert (await _tools()["cron_delete"].ainvoke({"job_id": job_id})).startswith("Error:")
    assert (await _tools()["cron_run"].ainvoke({"job_id": job_id})).startswith("Error:")

    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        assert job.name == name

    assert client.delete(f"/api/agents/{other['id']}/permanent").status_code == 200


async def test_run_tool_creates_one_run_and_reports_busy(client) -> None:
    tools = _tools()
    name = _name("run")
    job_id = _job_id(
        await tools["cron_create"].ainvoke(
            {"name": name, "prompt": "立即运行测试", "schedule": "0 8 * * *"}
        )
    )

    first = await tools["cron_run"].ainvoke({"job_id": job_id})
    assert "run_id:" in first

    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        assert job is not None
        runs = (
            await db.scalars(select(Run).where(Run.session_id == job.session_id))
        ).all()
        assert len(runs) == 1
        assert runs[0].source == "cron"
        assert runs[0].status == "queued"
        run_id = runs[0].id

    busy = await tools["cron_run"].ainvoke({"job_id": job_id})
    assert busy.startswith("Error:")
    assert "queued" in busy or "running" in busy

    client.post(f"/api/runs/{run_id}/cancel")
