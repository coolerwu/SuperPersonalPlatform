import asyncio
import json
import logging
import sqlite3
import stat

import pytest

import app.services.settings_store as settings_store_module
from app.services.settings_store import JsonStore, SettingsStore, build_default_settings


@pytest.mark.asyncio
async def test_json_store_atomic_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonStore(path)
    await store.update(lambda data: data.update({"runtime": {"session_retention_days": 7}}))
    assert json.loads(path.read_text("utf-8"))["runtime"]["session_retention_days"] == 7
    assert (tmp_path / "settings.json.bak").exists() is False


@pytest.mark.asyncio
async def test_settings_store_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{broken", "utf-8")
    store = SettingsStore(path)
    settings = await store.get()
    assert settings.runtime.session_retention_days == 30


@pytest.mark.asyncio
async def test_settings_store_keeps_bad_section_isolated(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"runtime": {"session_retention_days": "x"}, "agents": []}), "utf-8")
    settings = await SettingsStore(path).get()
    assert settings.runtime.session_retention_days == 30


@pytest.mark.asyncio
async def test_seed_default_settings_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_RETENTION_DAYS", "5")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("KB_WEBDAV_PASSWORD", "test-password")
    # The shared test database may contain a legacy app_configs row; this test
    # is about environment seeding, so keep the legacy migration out of it.
    monkeypatch.setattr(
        settings_store_module.app_settings,
        "app_database_url",
        f"sqlite+aiosqlite:///{tmp_path / 'no-legacy.db'}",
    )
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 5
    assert data["agents"][0]["id"] == "default"
    assert data["agents"][0]["provider"]["api_key"] == "test-key"
    assert data["knowledge"]["password"] == "test-password"


@pytest.mark.asyncio
async def test_seed_normalizes_provider_base_url(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("KB_WEBDAV_PASSWORD", "test-password")
    path = tmp_path / "settings.json"
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["agents"][0]["provider"]["base_url"] == "https://api.deepseek.com/anthropic"


def _legacy_config_db(tmp_path, value: int) -> str:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE app_configs (id TEXT PRIMARY KEY, session_retention_days INTEGER)"
        )
        connection.execute(
            "INSERT INTO app_configs (id, session_retention_days) VALUES ('default', ?)",
            (value,),
        )
    return f"sqlite+aiosqlite:///{path}"


@pytest.mark.asyncio
async def test_seed_migrates_legacy_retention_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_store_module.app_settings,
        "app_database_url",
        _legacy_config_db(tmp_path, 0),
    )
    path = tmp_path / "settings.json"
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 0


@pytest.mark.asyncio
async def test_seed_migrates_legacy_retention_value(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_store_module.app_settings,
        "app_database_url",
        _legacy_config_db(tmp_path, 7),
    )
    path = tmp_path / "settings.json"
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 7


@pytest.mark.asyncio
async def test_seed_ignores_missing_legacy_table(tmp_path, monkeypatch):
    database = tmp_path / "empty.db"
    with sqlite3.connect(database):
        pass
    monkeypatch.setattr(
        settings_store_module.app_settings,
        "app_database_url",
        f"sqlite+aiosqlite:///{database}",
    )
    path = tmp_path / "settings.json"
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 30


@pytest.mark.asyncio
async def test_existing_settings_file_is_not_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_store_module.app_settings,
        "app_database_url",
        _legacy_config_db(tmp_path, 0),
    )
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"runtime": {"session_retention_days": 12}}), "utf-8"
    )
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 12


@pytest.mark.asyncio
async def test_seed_keeps_existing_anthropic_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/anthropic/")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("KB_WEBDAV_PASSWORD", "test-password")
    path = tmp_path / "settings.json"
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["agents"][0]["provider"]["base_url"] == "https://api.deepseek.com/anthropic"


@pytest.mark.asyncio
async def test_ensure_seeded_keeps_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_RETENTION_DAYS", "5")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"runtime": {"session_retention_days": 12}}), "utf-8")
    await SettingsStore(path).ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 12
    assert "agents" not in data


@pytest.mark.asyncio
async def test_backup_holds_previous_content(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonStore(path)
    await store.update(lambda data: data.update({"runtime": {"session_retention_days": 7}}))
    assert (tmp_path / "settings.json.bak").exists() is False
    await store.update(lambda data: data.update({"runtime": {"session_retention_days": 8}}))
    backup = json.loads((tmp_path / "settings.json.bak").read_text("utf-8"))
    assert backup["runtime"]["session_retention_days"] == 7


@pytest.mark.asyncio
async def test_backup_recovers_and_heals_main_file(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.update_runtime(session_retention_days=7)
    await store.update_runtime(session_retention_days=8)
    path.write_text("{broken", "utf-8")

    settings = await store.get()

    assert settings.runtime.session_retention_days == 7
    healed = json.loads(path.read_text("utf-8"))
    assert healed["runtime"]["session_retention_days"] == 7
    backup = json.loads((tmp_path / "settings.json.bak").read_text("utf-8"))
    assert backup["runtime"]["session_retention_days"] == 7


@pytest.mark.asyncio
async def test_corrupt_main_does_not_overwrite_good_backup(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.update_runtime(session_retention_days=7)
    await store.update_runtime(session_retention_days=8)
    path.write_text("{broken", "utf-8")

    await store.update_runtime(session_retention_days=9)

    backup = json.loads((tmp_path / "settings.json.bak").read_text("utf-8"))
    assert backup["runtime"]["session_retention_days"] == 7
    assert (await store.get()).runtime.session_retention_days == 9


@pytest.mark.asyncio
async def test_settings_files_are_private(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.update_runtime(session_retention_days=7)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    await store.update_runtime(session_retention_days=8)
    backup = tmp_path / "settings.json.bak"
    assert backup.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_concurrent_updates_are_serialized(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonStore(path)

    async def add(value: int) -> None:
        await store.update(lambda data: data.setdefault("seen", []).append(value))

    await asyncio.gather(*(add(value) for value in range(20)))
    data = json.loads(path.read_text("utf-8"))
    assert sorted(data["seen"]) == list(range(20))


@pytest.mark.asyncio
async def test_corrupt_file_logs_warning_without_traceback(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text("{broken", "utf-8")
    store = SettingsStore(path)

    with caplog.at_level(logging.WARNING, logger="app.services.settings_store"):
        await store.get()

    records = [
        record
        for record in caplog.records
        if "cannot read settings file" in record.getMessage()
    ]
    assert records
    assert all(record.exc_info is None for record in records)


@pytest.mark.asyncio
async def test_update_runtime_rejects_unknown_and_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    with pytest.raises(ValueError):
        await store.update_runtime(unknown_field=1)
    with pytest.raises(ValueError):
        await store.update_runtime(session_retention_days=-1)
    assert not path.exists()


@pytest.mark.asyncio
async def test_update_knowledge_rejects_unknown_and_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    with pytest.raises(ValueError):
        await store.update_knowledge(unknown_field=1)
    with pytest.raises(ValueError):
        await store.update_knowledge(interval_seconds=0)
    assert not path.exists()


@pytest.mark.asyncio
async def test_mutate_raw_persists_arbitrary_changes(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.ensure_seeded()

    def mutate(data):
        data["channel_conversations"] = {}

    returned = await store.mutate_raw(mutate)
    assert returned["channel_conversations"] == {}
    assert json.loads(path.read_text("utf-8"))["channel_conversations"] == {}


@pytest.mark.asyncio
async def test_channel_field_updates_do_not_overwrite_each_other(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.mutate_raw(
        lambda data: data.update(
            {
                "channels": [
                    {"id": "ch1", "agent_id": "default", "status": "active"}
                ]
            }
        )
    )

    await asyncio.gather(
        store.update_channel_fields("ch1", sync_buf="cursor"),
        store.update_channel_fields(
            "ch1", last_seen_at="2026-01-01T00:00:00+00:00"
        ),
        store.update_channel_fields(
            "ch1", status="expired", last_error="login expired"
        ),
    )

    channel = (await store.get()).channels[0]
    assert channel.sync_buf == "cursor"
    assert channel.last_seen_at == "2026-01-01T00:00:00+00:00"
    assert channel.status == "expired"
    assert channel.last_error == "login expired"


@pytest.mark.asyncio
async def test_channel_field_update_rejects_unknown_fields(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="unknown channel field"):
        await store.update_channel_fields("ch1", bogus="x")


@pytest.mark.asyncio
async def test_channel_field_update_rejects_invalid_value(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.mutate_raw(
        lambda data: data.update(
            {
                "channels": [
                    {"id": "ch1", "agent_id": "default", "status": "active"}
                ]
            }
        )
    )

    with pytest.raises(ValueError):
        await store.update_channel_fields("ch1", last_seen_at=123)

    channel = (await store.get()).channels[0]
    assert channel.last_seen_at is None
