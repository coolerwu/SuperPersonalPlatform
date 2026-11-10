import json

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
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    await store.ensure_seeded()
    data = json.loads(path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 5
    assert data["agents"][0]["id"] == "default"
