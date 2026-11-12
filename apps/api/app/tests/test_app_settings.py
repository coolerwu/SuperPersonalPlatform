from __future__ import annotations

import json

from app.services.claude_settings import CLEANUP_DISABLED_DAYS, settings_path


def test_settings_api_default_and_update(client) -> None:
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )

    body = client.get("/api/settings").json()
    assert body == {"session_retention_days": 30, "task_budget_tokens": 0}

    response = client.put(
        "/api/settings",
        json={"session_retention_days": 7, "task_budget_tokens": 32000},
    )
    assert response.status_code == 200
    assert response.json() == {
        "session_retention_days": 7,
        "task_budget_tokens": 32000,
    }
    assert client.get("/api/settings").json()["task_budget_tokens"] == 32000

    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": -1, "task_budget_tokens": 0},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 3651, "task_budget_tokens": 0},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": -1},
        ).status_code
        == 422
    )
    response = client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 19999},
    )
    assert response.status_code == 422
    assert "任务预算" in response.json()["detail"][0]["msg"]
    assert (
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 1_000_001},
        ).status_code
        == 422
    )

    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )


def test_settings_roundtrip(client) -> None:
    response = client.put(
        "/api/settings",
        json={"session_retention_days": 7, "task_budget_tokens": 0},
    )
    assert response.status_code == 200
    assert client.get("/api/settings").json()["session_retention_days"] == 7
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )


def test_settings_persist_to_settings_file(client) -> None:
    store = client.app.state.settings_store
    client.put(
        "/api/settings",
        json={"session_retention_days": 9, "task_budget_tokens": 0},
    )
    assert store.path.is_file()
    data = json.loads(store.path.read_text("utf-8"))
    assert data["runtime"]["session_retention_days"] == 9
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )


def test_settings_sync_cleanup_period_to_claude(client) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": {"a@b": True}}), "utf-8")
    try:
        client.put(
            "/api/settings",
            json={"session_retention_days": 7, "task_budget_tokens": 0},
        )
        data = json.loads(path.read_text("utf-8"))
        assert data["cleanupPeriodDays"] == 7
        assert data["enabledPlugins"] == {"a@b": True}

        client.put(
            "/api/settings",
            json={"session_retention_days": 0, "task_budget_tokens": 0},
        )
        assert json.loads(path.read_text("utf-8"))["cleanupPeriodDays"] == (
            CLEANUP_DISABLED_DAYS
        )

        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 0},
        )
        assert json.loads(path.read_text("utf-8"))["cleanupPeriodDays"] == 30
    finally:
        # Do not leak a fake plugin into the shared test home.
        path.write_text("{}\n", "utf-8")
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 0},
        )


def test_settings_update_reports_unwritable_claude_settings(client) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", "utf-8")
    try:
        response = client.put(
            "/api/settings",
            json={"session_retention_days": 7, "task_budget_tokens": 0},
        )
        assert response.status_code == 500
        assert "Claude 设置" in response.json()["detail"]
    finally:
        path.write_text("{}\n", "utf-8")
        client.put(
            "/api/settings",
            json={"session_retention_days": 30, "task_budget_tokens": 0},
        )


def test_task_budget_persists_to_settings_file(client) -> None:
    store = client.app.state.settings_store
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 50000},
    )
    data = json.loads(store.path.read_text("utf-8"))
    assert data["runtime"]["task_budget_tokens"] == 50000
    client.put(
        "/api/settings",
        json={"session_retention_days": 30, "task_budget_tokens": 0},
    )
