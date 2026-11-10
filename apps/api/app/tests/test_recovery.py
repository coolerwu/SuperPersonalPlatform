import pytest

from app.db import init_db
from app.services import recovery, turn_lease
from app.services.claude_runner import transcript_path


class FakeStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(agents=[AgentSection(id="default", name="Assistant")])


class FakeRegistry:
    def __init__(self):
        self.turns = []

    async def submit(self, turn):
        self.turns.append(turn)
        return turn.id


@pytest.mark.asyncio
async def test_recovery_resubmits_only_existing_transcripts(tmp_path):
    await init_db()
    path = transcript_path(
        "11111111-1111-1111-1111-111111111111", "default", home=tmp_path
    )
    path.parent.mkdir(parents=True)
    path.write_text("{}", "utf-8")
    await turn_lease.add("11111111-1111-1111-1111-111111111111")
    await turn_lease.add("22222222-2222-2222-2222-222222222222")
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(registry, FakeStore(), home=tmp_path)
        assert [turn.session_id for turn in registry.turns] == [
            "11111111-1111-1111-1111-111111111111"
        ]
        assert registry.turns[0].source == "resume"
        assert registry.turns[0].agent_id == "default"
        assert await turn_lease.is_running(
            "22222222-2222-2222-2222-222222222222"
        ) is False
    finally:
        await turn_lease.remove("11111111-1111-1111-1111-111111111111")
        await turn_lease.remove("22222222-2222-2222-2222-222222222222")


@pytest.mark.asyncio
async def test_recovery_drops_invalid_session_ids(tmp_path):
    await init_db()
    await turn_lease.add("not-a-uuid")
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(registry, FakeStore(), home=tmp_path)
        assert registry.turns == []
        assert await turn_lease.is_running("not-a-uuid") is False
    finally:
        await turn_lease.remove("not-a-uuid")
