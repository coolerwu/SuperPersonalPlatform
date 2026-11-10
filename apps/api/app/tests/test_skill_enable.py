from __future__ import annotations

import pytest

import app.services.settings_store as settings_store_module
from app.services import skill_tools
from app.services.settings_store import SettingsStore, build_default_settings


@pytest.mark.asyncio
async def test_enable_skill_for_agent_reports_missing_and_appends(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    await store.mutate_raw(lambda data: data.update(build_default_settings()))
    monkeypatch.setattr(
        settings_store_module, "get_settings_store", lambda: store
    )

    assert await skill_tools._enable_skill_for_agent("missing", "demo") is False
    assert await skill_tools._enable_skill_for_agent("default", "demo") is True
    assert await skill_tools._enable_skill_for_agent("default", "demo") is True

    settings = await store.get()
    assert settings.agents[0].enabled_skills == ["demo"]
