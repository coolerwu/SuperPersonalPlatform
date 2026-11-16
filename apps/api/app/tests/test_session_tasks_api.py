import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SESSION_ID = "55555555-5555-5555-5555-555555555555"


class FakeTaskService:
    def __init__(self, *, snapshot=None, events=None):
        self.snapshot_value = snapshot or {
            "has_turn": True,
            "updated_at": "2026-11-16T12:00:00+00:00",
            "tasks": [],
        }
        self.events = list(events or [])
        self.queue = None
        self.subscribed = []
        self.unsubscribed = []

    async def snapshot(self, session_id):
        return self.snapshot_value

    def subscribe(self, session_id):
        self.subscribed.append(session_id)
        self.queue = asyncio.Queue()
        for event in self.events:
            self.queue.put_nowait(event)
        return self.queue

    def unsubscribe(self, session_id, queue):
        self.unsubscribed.append((session_id, queue))


class FailingTaskService(FakeTaskService):
    async def snapshot(self, session_id):
        raise RuntimeError("snapshot failed")


def test_session_tasks_snapshot(client):
    service = FakeTaskService(
        snapshot={
            "has_turn": False,
            "updated_at": None,
            "tasks": [
                {
                    "task_id": "t1",
                    "tool_use_id": None,
                    "description": "回测",
                    "status": "running",
                    "summary": "",
                    "last_tool_name": "Bash",
                    "usage": {"tool_uses": 1},
                    "started_at": "2026-11-16T12:00:00+00:00",
                    "updated_at": "2026-11-16T12:00:01+00:00",
                    "completed_at": None,
                }
            ],
        }
    )
    client.app.state.session_tasks = service

    response = client.get(f"/api/sessions/{SESSION_ID}/tasks")

    assert response.status_code == 200
    body = response.json()
    assert body["tasks"][0]["task_id"] == "t1"
    assert body["tasks"][0]["status"] == "running"


def test_session_tasks_requires_auth():
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert (
            anonymous.get(f"/api/sessions/{SESSION_ID}/tasks").status_code
            == 401
        )


def test_session_tasks_without_service_is_503(client):
    client.app.state.session_tasks = None
    assert (
        client.get(f"/api/sessions/{SESSION_ID}/tasks").status_code == 503
    )


def test_session_events_without_service_is_503(client):
    client.app.state.session_tasks = None
    assert (
        client.get(f"/api/sessions/{SESSION_ID}/events").status_code == 503
    )


def test_session_events_unsubscribes_when_snapshot_fails(client):
    service = FailingTaskService()
    client.app.state.session_tasks = service

    with pytest.raises(RuntimeError):
        client.get(f"/api/sessions/{SESSION_ID}/events")

    assert service.unsubscribed == [(SESSION_ID, service.queue)]


def test_session_events_sends_snapshot_then_events(client):
    service = FakeTaskService(
        snapshot={"has_turn": True, "updated_at": None, "tasks": []},
        events=[
            {"type": "task_started", "task_id": "t1"},
            None,
        ],
    )
    client.app.state.session_tasks = service

    with client.stream(
        "GET", f"/api/sessions/{SESSION_ID}/events"
    ) as response:
        assert response.status_code == 200
        lines = [
            line
            for line in response.iter_lines()
            if line and line.startswith("data: ")
        ]

    payloads = [json.loads(line[len("data: "):]) for line in lines]
    assert payloads[0]["type"] == "tasks_snapshot"
    assert payloads[0]["has_turn"] is True
    assert payloads[1]["type"] == "task_started"
    assert service.subscribed == [SESSION_ID]
    assert service.unsubscribed == [(SESSION_ID, service.queue)]
