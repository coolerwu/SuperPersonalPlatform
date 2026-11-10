from __future__ import annotations

import pytest

from app.config import settings
from app.services.settings_store import AgentSection, PlatformSettings
from app.services.workspace import ensure_agent_workspaces


class FakeStore:
    def __init__(self, agents: list[AgentSection]) -> None:
        self._settings = PlatformSettings(agents=agents)

    async def get(self) -> PlatformSettings:
        return self._settings


@pytest.mark.asyncio
async def test_ensure_agent_workspaces_creates_settings_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_workspaces_dir", tmp_path)
    store = FakeStore(
        [
            AgentSection(id="alpha", name="Alpha"),
            AgentSection(id="beta-2", name="Beta"),
            # Settings files are user-editable; a malformed id must not escape
            # the workspace root or abort startup.
            AgentSection(id="../escape", name="Bad"),
        ]
    )

    await ensure_agent_workspaces(store)

    assert (tmp_path / "alpha").is_dir()
    assert (tmp_path / "beta-2").is_dir()
    assert not (tmp_path.parent / "escape").exists()
