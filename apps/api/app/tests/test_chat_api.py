import pytest


@pytest.fixture
def fake_registry(client):
    class FakeRegistry:
        def __init__(self):
            self.turns = []

        async def submit(self, turn):
            self.turns.append(turn)
            return "turn-1"

        def is_busy(self, session_id):
            return False

    registry = FakeRegistry()
    client.app.state.registry = registry
    return registry


def test_send_message_enqueues(client, fake_registry):
    response = client.post(
        "/api/chat/messages",
        json={
            "agent_id": "default",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "content": "hi",
        },
    )
    assert response.status_code == 201
    assert response.json()["turn_id"] == "turn-1"
    assert fake_registry.turns[0].prompt == "hi"


def test_interrupt_endpoint(client):
    class FakeRegistry:
        async def interrupt(self, session_id):
            return True

    client.app.state.registry = FakeRegistry()
    response = client.post("/api/sessions/11111111-1111-1111-1111-111111111111/interrupt")
    assert response.status_code == 200
    assert response.json() == {"interrupted": True}


def test_delete_busy_session_conflicts(client):
    class FakeRegistry:
        def is_busy(self, session_id):
            return True

    client.app.state.registry = FakeRegistry()
    response = client.delete(
        "/api/sessions/11111111-1111-1111-1111-111111111111?agent_id=default"
    )
    assert response.status_code == 409
