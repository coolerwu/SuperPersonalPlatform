from __future__ import annotations

import os
import time
import uuid

from app.db import SessionLocal
from app.models import AppConfig
from app.services import sdk_bridge
from app.services.claude_runner import transcript_dir
from app.services.session_service import SessionCleanupRuntime


async def _reset_config() -> None:
    async with SessionLocal() as db:
        row = await db.get(AppConfig, "default")
        if row is not None:
            await db.delete(row)
            await db.commit()


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


async def test_cleanup_reads_configured_retention(client, monkeypatch) -> None:
    store = client.app.state.settings_store
    original = (await store.get()).runtime.session_retention_days
    old_id = str(uuid.uuid4())
    fresh_id = str(uuid.uuid4())
    directory = transcript_dir("default")
    directory.mkdir(parents=True, exist_ok=True)
    old_path = directory / f"{old_id}.jsonl"
    fresh_path = directory / f"{fresh_id}.jsonl"
    old_path.write_text("{}", "utf-8")
    fresh_path.write_text("{}", "utf-8")
    stamp = time.time() - 400 * 86400
    os.utime(old_path, (stamp, stamp))
    deleted: list[str] = []

    def fake_delete(session_id: str) -> None:
        deleted.append(session_id)
        (directory / f"{session_id}.jsonl").unlink(missing_ok=True)

    monkeypatch.setattr(sdk_bridge, "delete_session", fake_delete)
    await store.update_runtime(session_retention_days=1)
    try:
        runtime = SessionCleanupRuntime(store=store, interval_seconds=60)
        removed = await runtime.run_once()

        assert removed >= 1
        assert old_id in deleted
        assert fresh_id not in deleted
        assert not old_path.exists()
        assert fresh_path.exists()
    finally:
        await store.update_runtime(session_retention_days=original)
        old_path.unlink(missing_ok=True)
        fresh_path.unlink(missing_ok=True)
