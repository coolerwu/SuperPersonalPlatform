import pytest
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.models import ChannelDelivery
from app.services import channel_delivery, recovery, turn_lease
from app.services.claude_runner import transcript_path


class FakeStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(agents=[AgentSection(id="default", name="Assistant")])


class EmptyAgentStore:
    async def get(self):
        from app.services.settings_store import PlatformSettings

        return PlatformSettings(agents=[])


class TwoAgentStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(
            agents=[
                AgentSection(id="default", name="Assistant"),
                AgentSection(id="second", name="Second"),
            ]
        )


class InvalidAgentStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(
            agents=[AgentSection(id="../escape", name="Escape")]
        )


class PausedAgentStore:
    async def get(self):
        from app.services.settings_store import AgentSection, PlatformSettings

        return PlatformSettings(
            agents=[AgentSection(id="default", name="Assistant", status="paused")]
        )


class RaisingStore:
    async def get(self):
        raise RuntimeError("settings unavailable")


class FakeRegistry:
    def __init__(self):
        self.turns = []

    async def submit(self, turn):
        self.turns.append(turn)
        return turn.id


class PartialRegistry(FakeRegistry):
    def __init__(self, fail_session_id):
        super().__init__()
        self._fail_session_id = fail_session_id

    async def submit(self, turn):
        if turn.session_id == self._fail_session_id:
            raise RuntimeError("registry unavailable")
        return await super().submit(turn)


def _write_transcript(session_id: str, agent_id: str, tmp_path):
    path = transcript_path(session_id, agent_id, home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", "utf-8")
    return path


@pytest.mark.asyncio
async def test_recovery_resubmits_only_existing_transcripts(tmp_path):
    await init_db()
    session_id = "11111111-1111-1111-1111-111111111111"
    _write_transcript(session_id, "default", tmp_path)
    await turn_lease.add(session_id)
    await turn_lease.add("22222222-2222-2222-2222-222222222222")
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(registry, FakeStore(), home=tmp_path)
        assert [turn.session_id for turn in registry.turns] == [session_id]
        assert registry.turns[0].source == "resume"
        assert registry.turns[0].agent_id == "default"
        # The fake registry never runs the turn, so recovery leaves the lease.
        assert await turn_lease.is_running(session_id) is True
        assert await turn_lease.is_running(
            "22222222-2222-2222-2222-222222222222"
        ) is False
    finally:
        await turn_lease.remove(session_id)
        await turn_lease.remove("22222222-2222-2222-2222-222222222222")


@pytest.mark.asyncio
async def test_recovery_attributes_session_to_transcript_owner(tmp_path):
    await init_db()
    session_id = "33333333-3333-3333-3333-333333333333"
    _write_transcript(session_id, "second", tmp_path)
    await turn_lease.add(session_id)
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(
            registry, TwoAgentStore(), home=tmp_path
        )
        assert [(turn.session_id, turn.agent_id) for turn in registry.turns] == [
            (session_id, "second")
        ]
        assert await turn_lease.is_running(session_id) is True
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_continues_after_submit_failure(tmp_path):
    await init_db()
    failing = "44444444-4444-4444-4444-444444444444"
    succeeding = "55555555-5555-5555-5555-555555555555"
    for session_id in (failing, succeeding):
        _write_transcript(session_id, "default", tmp_path)
        await turn_lease.add(session_id)
    registry = PartialRegistry(failing)
    try:
        await recovery.recover_running_sessions(registry, FakeStore(), home=tmp_path)
        assert [turn.session_id for turn in registry.turns] == [succeeding]
        # The failed row keeps its lease so the next startup can retry it.
        assert await turn_lease.is_running(failing) is True
        assert await turn_lease.is_running(succeeding) is True
    finally:
        await turn_lease.remove(failing)
        await turn_lease.remove(succeeding)


@pytest.mark.asyncio
async def test_recovery_keeps_leases_when_no_agents(tmp_path):
    await init_db()
    session_id = "66666666-6666-6666-6666-666666666666"
    await turn_lease.add(session_id)
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(
            registry, EmptyAgentStore(), home=tmp_path
        )
        assert registry.turns == []
        assert await turn_lease.is_running(session_id) is True
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_skips_invalid_agent_ids(tmp_path):
    await init_db()
    session_id = "77777777-7777-7777-7777-777777777777"
    await turn_lease.add(session_id)
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(
            registry, InvalidAgentStore(), home=tmp_path
        )
        assert registry.turns == []
        # No valid agent can own the transcript, so the lease is dropped.
        assert await turn_lease.is_running(session_id) is False
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_keeps_leases_owned_by_inactive_agents(tmp_path):
    await init_db()
    session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _write_transcript(session_id, "default", tmp_path)
    await turn_lease.add(session_id)
    registry = FakeRegistry()
    try:
        await recovery.recover_running_sessions(
            registry, PausedAgentStore(), home=tmp_path
        )
        # A paused agent is not woken now, but re-enabling it must still be
        # able to resume the session, so the lease stays.
        assert registry.turns == []
        assert await turn_lease.is_running(session_id) is True
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_survives_settings_failure(tmp_path):
    await init_db()
    session_id = "88888888-8888-8888-8888-888888888888"
    await turn_lease.add(session_id)
    try:
        # Must not raise and must not drop the lease.
        await recovery.recover_running_sessions(
            FakeRegistry(), RaisingStore(), home=tmp_path
        )
        assert await turn_lease.is_running(session_id) is True
    finally:
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_survives_lease_listing_failure(tmp_path, monkeypatch):
    await init_db()
    session_id = "99999999-9999-9999-9999-999999999999"
    await turn_lease.add(session_id)

    async def boom():
        raise RuntimeError("lease table unavailable")

    monkeypatch.setattr(turn_lease, "list_all", boom)
    try:
        # Must not raise and must not drop the lease.
        await recovery.recover_running_sessions(
            FakeRegistry(), FakeStore(), home=tmp_path
        )
        assert await turn_lease.is_running(session_id) is True
    finally:
        await turn_lease.remove(session_id)


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


async def _create_delivery(session_id: str) -> int:
    async with SessionLocal() as db:
        delivery_id = await channel_delivery.create_pending(
            db, channel_id="ch1", external_id="wx-1", session_id=session_id
        )
        await db.commit()
    return delivery_id


async def _cleanup_deliveries(session_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(ChannelDelivery).where(ChannelDelivery.session_id == session_id)
        )
        await db.commit()


@pytest.mark.asyncio
async def test_recovery_drops_orphan_deliveries_with_dropped_lease(tmp_path):
    await init_db()
    session_id = "bbbbbbbb-cccc-cccc-cccc-cccccccccccc"
    await turn_lease.add(session_id)
    empty_id = await _create_delivery(session_id)
    filled_id = await _create_delivery(session_id)
    await channel_delivery.complete(filled_id, "already owed")
    try:
        # No transcript, so recovery drops the lease.
        await recovery.recover_running_sessions(
            FakeRegistry(), FakeStore(), home=tmp_path
        )
        assert await turn_lease.is_running(session_id) is False
        assert await channel_delivery.get(empty_id) is None
        # A filled row is already owed to the sender; recovery keeps it.
        assert await channel_delivery.get(filled_id) is not None
    finally:
        await _cleanup_deliveries(session_id)
        await turn_lease.remove(session_id)


@pytest.mark.asyncio
async def test_recovery_keeps_orphan_deliveries_when_lease_is_kept(tmp_path):
    await init_db()
    session_id = "bbbbbbbb-dddd-dddd-dddd-dddddddddddd"
    _write_transcript(session_id, "default", tmp_path)
    await turn_lease.add(session_id)
    empty_id = await _create_delivery(session_id)
    try:
        # A paused agent keeps its lease for a later startup, so the empty
        # obligation must survive too.
        await recovery.recover_running_sessions(
            FakeRegistry(), PausedAgentStore(), home=tmp_path
        )
        assert await turn_lease.is_running(session_id) is True
        assert await channel_delivery.get(empty_id) is not None
    finally:
        await _cleanup_deliveries(session_id)
        await turn_lease.remove(session_id)
