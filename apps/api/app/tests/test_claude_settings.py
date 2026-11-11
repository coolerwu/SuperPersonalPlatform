from __future__ import annotations

import json
import stat

import pytest

from app.services import plugins
from app.services.claude_settings import (
    CLEANUP_DISABLED_DAYS,
    ClaudeSettingsError,
    cleanup_period_days,
    client_ttl_exceeds_retention,
    config_lock,
    settings_path,
    sync_cleanup_period,
)


def test_cleanup_period_mapping() -> None:
    assert cleanup_period_days(0) == CLEANUP_DISABLED_DAYS
    assert cleanup_period_days(1) == 1
    assert cleanup_period_days(30) == 30


def test_client_ttl_must_be_shorter_than_retention() -> None:
    assert client_ttl_exceeds_retention(300, 30) is False
    assert client_ttl_exceeds_retention(300, 0) is False
    assert client_ttl_exceeds_retention(7 * 86400, 1) is True


@pytest.mark.asyncio
async def test_sync_writes_and_preserves_existing_keys(tmp_path) -> None:
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"enabledPlugins": {"a@b": True}, "cleanupPeriodDays": 1}),
        "utf-8",
    )

    assert await sync_cleanup_period(7, home=tmp_path) is True

    data = json.loads(path.read_text("utf-8"))
    assert data == {"enabledPlugins": {"a@b": True}, "cleanupPeriodDays": 7}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_sync_skips_an_unchanged_value(tmp_path) -> None:
    assert await sync_cleanup_period(30, home=tmp_path) is True
    path = settings_path(tmp_path)
    stamp = path.stat().st_mtime_ns

    assert await sync_cleanup_period(30, home=tmp_path) is False
    assert path.stat().st_mtime_ns == stamp


@pytest.mark.asyncio
async def test_sync_writes_disabled_retention_as_a_long_period(tmp_path) -> None:
    await sync_cleanup_period(0, home=tmp_path)
    data = json.loads(settings_path(tmp_path).read_text("utf-8"))
    assert data["cleanupPeriodDays"] == CLEANUP_DISABLED_DAYS


@pytest.mark.asyncio
async def test_sync_never_replaces_a_corrupt_settings_file(tmp_path) -> None:
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", "utf-8")

    with pytest.raises(ClaudeSettingsError):
        await sync_cleanup_period(7, home=tmp_path)

    assert path.read_text("utf-8") == "{not json"


@pytest.mark.asyncio
async def test_sync_fails_fast_while_another_writer_holds_the_lock(
    tmp_path, monkeypatch
) -> None:
    from app.services import claude_settings

    monkeypatch.setattr(claude_settings, "LOCK_WAIT_SECONDS", 0.05)
    lock = config_lock()
    await lock.acquire()
    try:
        with pytest.raises(ClaudeSettingsError):
            await sync_cleanup_period(7, home=tmp_path)
    finally:
        lock.release()


@pytest.mark.asyncio
async def test_plugin_mutations_share_the_settings_lock() -> None:
    assert plugins._lock() is config_lock()
