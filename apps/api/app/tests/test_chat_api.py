import functools

import pytest

from app.services import commands
from app.services.claude_runner import transcript_path
from app.services.session_registry import SessionDeletingError
from app.services.turn_stream import TurnStream

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def _store(client):
    return client.app.state.settings_store


def _call(client, function, /, *args, **kwargs):
    return client.portal.call(functools.partial(function, *args, **kwargs))


class RecordingRegistry:
    def __init__(self, *, busy=False, interrupted=False, submit_error=None):
        self.turns = []
        self.busy = busy
        self.interrupted = interrupted
        self.interrupt_calls = []
        self.closed = []
        self.submit_error = submit_error

    async def submit(self, turn):
        if self.submit_error is not None:
            raise self.submit_error
        self.turns.append(turn)
        return "turn-1"

    def is_busy(self, session_id):
        return self.busy

    async def interrupt(self, session_id):
        self.interrupt_calls.append(session_id)
        return self.interrupted

    async def close_session(self, session_id):
        self.closed.append(session_id)


class RecordingCatalog:
    def __init__(self, *, deleted=True, messages=None, rename_error=None):
        self.deleted = deleted
        self.rows = messages or []
        self.rename_error = rename_error
        self.deleted_ids = []
        self.message_calls = []
        self.rename_calls = []

    async def delete(self, agent_id, session_id):
        self.deleted_ids.append((agent_id, session_id))
        return self.deleted

    async def messages(self, session_id, *, agent_id=None):
        self.message_calls.append((session_id, agent_id))
        return self.rows

    async def rename(self, session_id, title, *, agent_id=None):
        self.rename_calls.append((session_id, title, agent_id))
        if self.rename_error is not None:
            raise self.rename_error


@pytest.fixture
def fake_registry(client):
    registry = RecordingRegistry()
    client.app.state.registry = registry
    return registry


def test_send_message_enqueues(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={
            "agent_id": "default",
            "session_id": SESSION_ID,
            "content": "  hi  ",
        },
    )
    assert response.status_code == 201
    assert response.json() == {
        "session_id": SESSION_ID,
        "turn_id": "turn-1",
        "accepted": True,
    }
    turn = fake_registry.turns[0]
    assert turn.prompt == "hi"
    assert turn.session_id == SESSION_ID
    assert turn.agent_id == "default"
    assert turn.source == "manual"


@pytest.mark.parametrize("status", ["paused", "archived"])
def test_send_message_rejects_inactive_agent(client, fake_registry, status):
    agent = client.post("/api/agents", json={"name": f"Inactive-{status}"}).json()
    if status == "archived":
        assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    else:
        assert (
            client.put(
                f"/api/agents/{agent['id']}", json={"status": "paused"}
            ).status_code
            == 200
        )
    response = client.post(
        "/api/chat/messages",
        json={"agent_id": agent["id"], "session_id": SESSION_ID, "content": "hi"},
    )
    assert response.status_code == 409
    assert "暂停或归档" in response.json()["detail"]
    assert fake_registry.turns == []


def test_send_message_rejects_unknown_agent(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={"agent_id": "missing", "session_id": SESSION_ID, "content": "hi"},
    )
    assert response.status_code == 404
    assert fake_registry.turns == []


def test_send_message_rejects_session_owned_by_another_agent(client, fake_registry):
    other = client.post("/api/agents", json={"name": "Other"}).json()
    path = transcript_path(SESSION_ID, other["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", "utf-8")
    try:
        response = client.post(
            "/api/chat/messages",
            json={"agent_id": "default", "session_id": SESSION_ID, "content": "hi"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "session id belongs to another agent"
        assert fake_registry.turns == []
    finally:
        path.unlink(missing_ok=True)


def test_send_message_allows_the_session_owner(client, fake_registry):
    path = transcript_path(SESSION_ID, "default")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", "utf-8")
    try:
        response = client.post(
            "/api/chat/messages",
            json={"agent_id": "default", "session_id": SESSION_ID, "content": "hi"},
        )
        assert response.status_code == 201
    finally:
        path.unlink(missing_ok=True)


def test_send_message_rejects_session_being_deleted(client):
    client.app.state.registry = RecordingRegistry(
        submit_error=SessionDeletingError("session is being deleted")
    )
    response = client.post(
        "/api/chat/messages",
        json={"agent_id": "default", "session_id": SESSION_ID, "content": "hi"},
    )
    assert response.status_code == 409
    assert "being deleted" in response.json()["detail"]


def test_send_message_rejects_blank_content(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={"session_id": SESSION_ID, "content": "   "},
    )
    assert response.status_code == 400
    assert fake_registry.turns == []


def test_send_message_rejects_invalid_session_id(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={
            "agent_id": "agent-x",
            "session_id": "../../../etc/passwd",
            "content": "hi",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid session id"}
    assert fake_registry.turns == []


def test_send_message_without_registry_is_503(client):
    response = client.post(
        "/api/chat/messages",
        json={"session_id": SESSION_ID, "content": "hi"},
    )
    assert response.status_code == 503


def test_send_message_with_closed_registry_is_503(client):
    client.app.state.registry = RecordingRegistry(
        submit_error=RuntimeError("registry is closed")
    )
    response = client.post(
        "/api/chat/messages",
        json={"session_id": SESSION_ID, "content": "hi"},
    )
    assert response.status_code == 503


def test_interrupt_endpoint(client):
    client.app.state.registry = RecordingRegistry(interrupted=True)
    response = client.post(f"/api/sessions/{SESSION_ID}/interrupt")
    assert response.status_code == 200
    assert response.json() == {"interrupted": True}


def test_interrupt_without_registry_is_503(client):
    response = client.post(f"/api/sessions/{SESSION_ID}/interrupt")
    assert response.status_code == 503


def test_commands_without_registry_is_503(client):
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "command": "/help"},
    )
    assert response.status_code == 503


def test_help_command(client, fake_registry):
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "session_id": SESSION_ID, "command": "/help"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command"] == "/help"
    assert body["reply"] == commands.HELP_TEXT


def test_status_command(client, fake_registry):
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "session_id": SESSION_ID, "command": "/status"},
    )
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Agent：default" in reply
    assert f"会话：{SESSION_ID}" in reply
    assert "状态：空闲" in reply
    assert "None" not in reply


def test_status_command_without_session(client, fake_registry):
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "command": "/status"},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == commands.NO_SESSION_TEXT


def test_stop_command(client, fake_registry):
    fake_registry.interrupted = True
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "session_id": SESSION_ID, "command": "/stop"},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == commands.STOP_REQUESTED_TEXT
    assert fake_registry.interrupt_calls == [SESSION_ID]


def test_stop_command_without_session(client, fake_registry):
    response = client.post(
        "/api/chat/commands",
        json={"agent_id": "default", "command": "/stop"},
    )
    assert response.status_code == 200
    assert response.json()["reply"] == commands.STOP_EMPTY_TEXT
    assert fake_registry.interrupt_calls == []


def test_stream_without_registry_is_503(client):
    response = client.get(f"/api/sessions/{SESSION_ID}/stream")
    assert response.status_code == 503


def test_stream_without_active_turn_is_409(client, fake_registry):
    fake_registry.current_turn_id = lambda session_id: None
    response = client.get(f"/api/sessions/{SESSION_ID}/stream")
    assert response.status_code == 409


def test_stream_replays_events_and_unsubscribes(client):
    stream = TurnStream()
    stream.publish({"type": "assistant_delta", "delta": "hello"})
    stream.publish({"type": "turn_end", "status": "completed"})

    class StreamRegistry:
        def current_turn_id(self, session_id):
            return "turn-1"

        def stream(self, turn_id, session_id=None):
            if turn_id == "turn-1" and session_id == SESSION_ID:
                return stream
            return None

    client.app.state.registry = StreamRegistry()
    with client.stream("GET", f"/api/sessions/{SESSION_ID}/stream") as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "assistant_delta" in body
    assert "turn_end" in body
    assert stream._subscribers == set()


def test_delete_busy_session_conflicts(client):
    client.app.state.registry = RecordingRegistry(busy=True)
    response = client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")
    assert response.status_code == 409


def test_delete_success_closes_cached_client(client):
    registry = RecordingRegistry()
    catalog = RecordingCatalog(deleted=True)
    client.app.state.registry = registry
    client.app.state.session_catalog = catalog
    response = client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert catalog.deleted_ids == [("default", SESSION_ID)]
    assert registry.closed == [SESSION_ID]


def test_delete_missing_session_returns_404(client):
    registry = RecordingRegistry()
    client.app.state.registry = registry
    client.app.state.session_catalog = RecordingCatalog(deleted=False)
    response = client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")
    assert response.status_code == 404
    assert registry.closed == []


def test_rename_invalid_id_returns_400(client):
    client.app.state.session_catalog = RecordingCatalog(rename_error=ValueError("bad"))
    response = client.post(
        f"/api/sessions/{SESSION_ID}/rename?agent_id=default",
        json={"title": "new"},
    )
    assert response.status_code == 400


def test_rename_missing_session_returns_404(client):
    client.app.state.session_catalog = RecordingCatalog(
        rename_error=FileNotFoundError("missing")
    )
    response = client.post(
        f"/api/sessions/{SESSION_ID}/rename?agent_id=default",
        json={"title": "new"},
    )
    assert response.status_code == 404


def test_rename_requires_agent_id(client):
    client.app.state.session_catalog = RecordingCatalog()
    response = client.post(
        f"/api/sessions/{SESSION_ID}/rename",
        json={"title": "new"},
    )
    assert response.status_code == 422


def test_rename_success(client):
    catalog = RecordingCatalog()
    client.app.state.session_catalog = catalog
    response = client.post(
        f"/api/sessions/{SESSION_ID}/rename?agent_id=default",
        json={"title": " new "},
    )
    assert response.status_code == 200
    assert response.json() == {"renamed": True}
    assert catalog.rename_calls == [(SESSION_ID, "new", "default")]


def test_messages_requires_agent_id(client):
    client.app.state.session_catalog = RecordingCatalog()
    response = client.get(f"/api/sessions/{SESSION_ID}/messages")
    assert response.status_code == 422


def test_messages_returns_transcript(client):
    catalog = RecordingCatalog(
        messages=[{"id": "m1", "role": "user", "content": "hi", "blocks": []}]
    )
    client.app.state.session_catalog = catalog
    response = client.get(
        f"/api/sessions/{SESSION_ID}/messages?agent_id=default"
    )
    assert response.status_code == 200
    assert response.json() == [
        {"id": "m1", "role": "user", "content": "hi", "blocks": []}
    ]
    assert catalog.message_calls == [(SESSION_ID, "default")]


class DeletingRegistry(RecordingRegistry):
    def __init__(self, *, busy=False):
        super().__init__(busy=busy)
        self.deleting: set[str] = set()
        self.delete_attempts: list[str] = []
        self.finished: list[str] = []

    def begin_delete(self, session_id):
        self.delete_attempts.append(session_id)
        if self.busy:
            return False
        self.deleting.add(session_id)
        return True

    def finish_delete(self, session_id):
        self.finished.append(session_id)
        self.deleting.discard(session_id)


def test_delete_uses_atomic_begin_delete_and_finishes(client):
    registry = DeletingRegistry()
    client.app.state.registry = registry
    client.app.state.session_catalog = RecordingCatalog(deleted=True)

    response = client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")

    assert response.status_code == 200
    assert registry.delete_attempts == [SESSION_ID]
    assert registry.finished == [SESSION_ID]
    assert registry.deleting == set()


def test_delete_refused_when_begin_delete_fails(client):
    registry = DeletingRegistry(busy=True)
    client.app.state.registry = registry
    client.app.state.session_catalog = RecordingCatalog(deleted=True)

    response = client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")

    assert response.status_code == 409
    assert registry.finished == []
    registry.finish_delete(SESSION_ID)


def test_delete_releases_delete_marker_when_catalog_raises(client):
    class RaisingCatalog(RecordingCatalog):
        async def delete(self, agent_id, session_id):
            raise RuntimeError("sdk delete failed")

    registry = DeletingRegistry()
    client.app.state.registry = registry
    client.app.state.session_catalog = RaisingCatalog()

    with pytest.raises(RuntimeError):
        client.delete(f"/api/sessions/{SESSION_ID}?agent_id=default")

    assert registry.finished == [SESSION_ID]
    assert registry.deleting == set()
