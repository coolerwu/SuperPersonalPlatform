from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery, sdk_bridge, session_service
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_path
from app.services.session_catalog import SessionCatalog
from app.services.settings_store import SettingsStore


def _session_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.asyncio
async def test_artifacts_remove_screenshots_for_session(tmp_path):
    await init_db()
    session_id = _session_id()
    other_id = _session_id()
    screenshots = workspace_for("default", tmp_path) / "screenshots" / session_id
    screenshots.mkdir(parents=True)
    (screenshots / "shot.png").write_bytes(b"png")
    other = workspace_for("default", tmp_path) / "screenshots" / other_id
    other.mkdir(parents=True)

    await session_service.cleanup_session_artifacts(
        "default", session_id, home=tmp_path, store=None
    )

    assert not screenshots.exists()
    assert other.exists()


@pytest.mark.asyncio
async def test_artifacts_delete_channel_deliveries_for_session(tmp_path):
    await init_db()
    session_id = _session_id()
    other_id = _session_id()
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

        await session_service.cleanup_session_artifacts(
            "default", session_id, home=tmp_path, store=None
        )

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
async def test_artifacts_unbind_channel_conversations(tmp_path):
    await init_db()
    session_id = _session_id()
    other_id = _session_id()
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

    await session_service.cleanup_session_artifacts(
        "default", session_id, home=tmp_path, store=store
    )

    conversations = (await store.get()).channel_conversations
    assert "wx-1" not in conversations
    assert conversations["wx-2"].session_id == other_id


@pytest.mark.asyncio
async def test_artifact_steps_are_independent(tmp_path, monkeypatch):
    await init_db()
    session_id = _session_id()
    store = SettingsStore(tmp_path / "settings.json")
    await store.mutate_raw(
        lambda data: data.update(
            {
                "channel_conversations": {
                    "wx-1": {"session_id": session_id, "channel_id": "ch1"}
                }
            }
        )
    )
    screenshots = workspace_for("default", tmp_path) / "screenshots" / session_id
    screenshots.mkdir(parents=True)

    async def boom(session_id: str) -> int:
        raise RuntimeError("delivery cleanup failed")

    monkeypatch.setattr(channel_delivery, "delete_for_session", boom)

    await session_service.cleanup_session_artifacts(
        "default", session_id, home=tmp_path, store=store
    )

    # A failed delivery cleanup must not stop the other steps.
    assert not screenshots.exists()
    assert "wx-1" not in (await store.get()).channel_conversations


@pytest.mark.asyncio
async def test_real_sdk_delete_removes_transcript_and_subagents(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    session_id = _session_id()
    project = tmp_path / "claude" / "projects" / "default"
    project.mkdir(parents=True)
    transcript = project / f"{session_id}.jsonl"
    transcript.write_text('{"type": "user"}\n', "utf-8")
    subagents = project / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent.jsonl").write_text("{}\n", "utf-8")

    sdk_bridge.delete_session(session_id)

    assert not transcript.exists()
    assert not (project / session_id).exists()


@pytest.mark.asyncio
async def test_catalog_delete_removes_channel_deliveries(tmp_path, monkeypatch):
    await init_db()
    session_id = _session_id()

    class FakeStore:
        def __init__(self) -> None:
            self.raw = {"channel_conversations": {}}

        async def mutate_raw(self, mutate):
            mutate(self.raw)
            return self.raw

    path = transcript_path(session_id, "default", home=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", "utf-8")

    def fake_delete(session_id: str) -> None:
        path.unlink()

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db,
            channel_id="ch1",
            external_id="wx-1",
            session_id=session_id,
        )
        await db.commit()
    try:
        catalog = SessionCatalog(FakeStore(), home=tmp_path)
        assert await catalog.delete("default", session_id) is True
        assert await channel_delivery.get(delivery_id) is None
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ChannelDelivery).where(
                    ChannelDelivery.session_id == session_id
                )
            )
            await db.commit()
