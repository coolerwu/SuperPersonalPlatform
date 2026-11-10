import asyncio
import json
import logging
import stat

import pytest

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
