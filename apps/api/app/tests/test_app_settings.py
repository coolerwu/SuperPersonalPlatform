from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import AppConfig, Session, utcnow
from app.services.session_service import cleanup_inactive_sessions


async def _reset_config() -> None:
    async with SessionLocal() as db:
        row = await db.get(AppConfig, "default")
        if row is not None:
            await db.delete(row)
            await db.commit()


async def _make_session(last_message_at: datetime) -> str:
    async with SessionLocal() as db:
        session = Session(
            agent_id="default",
            title=f"settings-{uuid.uuid4().hex[:8]}",
            created_at=last_message_at,
            updated_at=last_message_at,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session.id


async def test_settings_api_default_and_update(client) -> None:
    await _reset_config()

    body = client.get("/api/settings").json()
    assert body["session_retention_days"] == 30
    assert body["session_cleanup_interval_seconds"] >= 60

    response = client.put("/api/settings", json={"session_retention_days": 7})
    assert response.status_code == 200
    assert response.json()["session_retention_days"] == 7
    assert client.get("/api/settings").json()["session_retention_days"] == 7

    assert (
        client.put("/api/settings", json={"session_retention_days": -1}).status_code
        == 422
    )
    assert (
        client.put("/api/settings", json={"session_retention_days": 3651}).status_code
        == 422
    )

    client.put("/api/settings", json={"session_retention_days": 30})


async def test_cleanup_reads_configured_retention(client) -> None:
    await _reset_config()
    client.put("/api/settings", json={"session_retention_days": 1})

    now = utcnow()
    old_id = await _make_session(now - timedelta(days=2))
    recent_id = await _make_session(now)

    removed = await cleanup_inactive_sessions(None, now=now)

    assert removed >= 1
    async with SessionLocal() as db:
        assert await db.get(Session, old_id) is None
        assert await db.get(Session, recent_id) is not None
    client.put("/api/settings", json={"session_retention_days": 30})
