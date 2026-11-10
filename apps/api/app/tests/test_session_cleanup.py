from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery, sdk_bridge, session_service, turn_lease
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_dir
from app.services.settings_store import (
    AgentSection,
    PlatformSettings,
    RuntimeSection,
    SettingsStore,
)


def _make_transcript(
    home, agent_id: str, session_id: str, *, age_days: float
) -> None:
    path = transcript_dir(agent_id, home=home) / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", "utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


def _install_fake_delete(monkeypatch, home, agent_id: str) -> list[str]:
    deleted: list[str] = []

    def fake_delete(session_id: str) -> None:
        deleted.append(session_id)
        transcript_dir(agent_id, home=home).joinpath(
            f"{session_id}.jsonl"
        ).unlink(missing_ok=True)

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    return deleted


@pytest.mark.asyncio
async def test_purge_deletes_old_keeps_fresh_and_running(tmp_path, monkeypatch):
    await init_db()
    old = f"old-{uuid.uuid4().hex[:8]}"
    fresh = f"fresh-{uuid.uuid4().hex[:8]}"
    running = f"running-{uuid.uuid4().hex[:8]}"
    _make_transcript(tmp_path, "default", old, age_days=400)
    _make_transcript(tmp_path, "default", fresh, age_days=1)
    _make_transcript(tmp_path, "default", running, age_days=400)
    deleted = _install_fake_delete(monkeypatch, tmp_path, "default")
    await turn_lease.add(running)
    try:
        removed = await session_service.purge_expired_transcripts(
            agent_id="default", retention_days=30, home=tmp_path
        )
    finally:
        await turn_lease.remove(running)

    directory = transcript_dir("default", home=tmp_path)
    assert removed == 1
    assert deleted == [old]
    assert not (directory / f"{old}.jsonl").exists()
    assert (directory / f"{fresh}.jsonl").exists()
    assert (directory / f"{running}.jsonl").exists()


@pytest.mark.asyncio
async def test_purge_removes_screenshots(tmp_path, monkeypatch):
    await init_db()
    session_id = f"shots-{uuid.uuid4().hex[:8]}"
    other_id = f"other-{uuid.uuid4().hex[:8]}"
    _make_transcript(tmp_path, "default", session_id, age_days=60)
    _install_fake_delete(monkeypatch, tmp_path, "default")
    screenshots = workspace_for("default", tmp_path) / "screenshots" / session_id
    screenshots.mkdir(parents=True)
    (screenshots / "shot.png").write_bytes(b"png")
    other_shots = workspace_for("default", tmp_path) / "screenshots" / other_id
    other_shots.mkdir(parents=True)

    removed = await session_service.purge_expired_transcripts(
        agent_id="default", retention_days=30, home=tmp_path
    )

    assert removed == 1
    assert not screenshots.exists()
    assert other_shots.exists()


@pytest.mark.asyncio
async def test_purge_deletes_channel_deliveries_for_session(tmp_path, monkeypatch):
    await init_db()
    session_id = f"delivery-{uuid.uuid4().hex[:8]}"
    other_id = f"delivery-{uuid.uuid4().hex[:8]}"
    _make_transcript(tmp_path, "default", session_id, age_days=60)
    _install_fake_delete(monkeypatch, tmp_path, "default")
    delivery_id = other_delivery_id = None
    try:
        async with SessionLocal() as db:
            delivery_id = await channel_delivery.create_pending(
                db,
                channel_id="ch1",
                external_id="wx-old",
                session_id=session_id,
            )
            other_delivery_id = await channel_delivery.create_pending(
                db,
                channel_id="ch1",
                external_id="wx-other",
                session_id=other_id,
            )
            await db.commit()

        removed = await session_service.purge_expired_transcripts(
            agent_id="default", retention_days=30, home=tmp_path
        )

        assert removed == 1
        assert await channel_delivery.get(delivery_id) is None
        assert await channel_delivery.get(other_delivery_id) is not None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(
                    ChannelDelivery.id.in_(
                        [item for item in (delivery_id, other_delivery_id) if item]
                    )
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_purge_unbinds_channel_conversations(tmp_path, monkeypatch):
    await init_db()
    session_id = f"conv-{uuid.uuid4().hex[:8]}"
    other_id = f"conv-{uuid.uuid4().hex[:8]}"
    _make_transcript(tmp_path, "default", session_id, age_days=60)
    _install_fake_delete(monkeypatch, tmp_path, "default")
    store = SettingsStore(tmp_path / "settings.json")
    await store.mutate_raw(
        lambda data: data.update(
            {
                "channel_conversations": {
                    "wx-1": {
                        "session_id": session_id,
                        "channel_id": "ch1",
                        "display_name": "Alice",
                    },
                    "wx-2": {
                        "session_id": other_id,
                        "channel_id": "ch1",
                        "display_name": "Bob",
                    },
                }
            }
        )
    )

    removed = await session_service.purge_expired_transcripts(
        agent_id="default", retention_days=30, home=tmp_path, store=store
    )

    assert removed == 1
    conversations = (await store.get()).channel_conversations
    assert "wx-1" not in conversations
    assert conversations["wx-2"].session_id == other_id


@pytest.mark.asyncio
async def test_purge_disabled_when_retention_is_zero(tmp_path, monkeypatch):
    await init_db()
    session_id = f"zero-{uuid.uuid4().hex[:8]}"
    _make_transcript(tmp_path, "default", session_id, age_days=400)
    deleted = _install_fake_delete(monkeypatch, tmp_path, "default")

    removed = await session_service.purge_expired_transcripts(
        agent_id="default", retention_days=0, home=tmp_path
    )

    assert removed == 0
    assert deleted == []
    assert (transcript_dir("default", home=tmp_path) / f"{session_id}.jsonl").exists()


class _FakeStore:
    def __init__(self, days: int = 7, agent_ids: list[str] | None = None) -> None:
        self.days = days
        self.agent_ids = agent_ids or ["agent-a", "agent-b"]
        self.raw = {"channel_conversations": {}}

    async def get(self) -> PlatformSettings:
        return PlatformSettings(
            runtime=RuntimeSection(session_retention_days=self.days),
            agents=[
                AgentSection(id=agent_id, name=agent_id)
                for agent_id in self.agent_ids
            ],
        )

    async def mutate_raw(self, mutate):
        mutate(self.raw)
        return self.raw


@pytest.mark.asyncio
async def test_runtime_run_once_uses_store_days_and_agents(monkeypatch):
    store = _FakeStore(days=7, agent_ids=["agent-a", "agent-b"])
    calls: list[tuple[str, int]] = []

    async def fake_purge(*, agent_id, retention_days, home=None, store=None):
        calls.append((agent_id, retention_days))
        return 2

    monkeypatch.setattr(session_service, "purge_expired_transcripts", fake_purge)
    runtime = session_service.SessionCleanupRuntime(store=store, interval_seconds=60)

    assert await runtime.run_once() == 4
    assert calls == [("agent-a", 7), ("agent-b", 7)]


@pytest.mark.asyncio
async def test_runtime_starts_and_stops(monkeypatch):
    async def fake_purge(*, agent_id, retention_days, home=None, store=None):
        return 0

    monkeypatch.setattr(session_service, "purge_expired_transcripts", fake_purge)
    runtime = session_service.SessionCleanupRuntime(
        store=_FakeStore(days=0), interval_seconds=60
    )
    await runtime.start()
    assert runtime._task is not None
    await runtime.stop()
