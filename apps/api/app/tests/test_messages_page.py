import sqlite3
from datetime import datetime, timedelta

from sqlalchemy.engine import make_url

from app.config import settings


def _db_path() -> str:
    database = make_url(settings.app_database_url).database
    assert database is not None
    return database


def _insert_message(
    conn: sqlite3.Connection,
    message_id: str,
    session_id: str,
    content: str,
    created_at: datetime,
    run_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, run_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            message_id,
            session_id,
            "user",
            content,
            run_id,
            created_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        ),
    )


def test_message_page_pagination(client) -> None:
    agent_id = client.get("/api/agents").json()[0]["id"]
    session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]
    other_session_id = client.post(f"/api/agents/{agent_id}/sessions", json={}).json()["id"]

    base = datetime(2026, 1, 1)
    with sqlite3.connect(_db_path()) as conn:
        for index in range(120):
            _insert_message(
                conn,
                f"msg{index:03d}",
                session_id,
                f"message {index}",
                base + timedelta(seconds=index),
            )
        _insert_message(conn, "other-msg", other_session_id, "other", base)

    latest = client.get(f"/api/sessions/{session_id}/messages/page?limit=50")
    assert latest.status_code == 200
    latest_body = latest.json()
    assert [item["content"] for item in latest_body["items"]][:1] == ["message 70"]
    assert [item["content"] for item in latest_body["items"]][-1:] == ["message 119"]
    assert len(latest_body["items"]) == 50
    assert latest_body["has_more"] is True
    assert latest_body["next_before"] == "msg070"

    older = client.get(
        f"/api/sessions/{session_id}/messages/page?limit=50&before=msg070"
    ).json()
    assert [item["content"] for item in older["items"]][:1] == ["message 20"]
    assert [item["content"] for item in older["items"]][-1:] == ["message 69"]
    assert len(older["items"]) == 50
    assert older["has_more"] is True
    assert older["next_before"] == "msg020"

    oldest = client.get(
        f"/api/sessions/{session_id}/messages/page?limit=50&before=msg020"
    ).json()
    assert [item["content"] for item in oldest["items"]][:1] == ["message 0"]
    assert [item["content"] for item in oldest["items"]][-1:] == ["message 19"]
    assert len(oldest["items"]) == 20
    assert oldest["has_more"] is False
    assert oldest["next_before"] is None

    # The old endpoint remains available for compatibility and one-off jumps.
    full = client.get(f"/api/sessions/{session_id}/messages").json()
    assert len(full) == 120

    assert (
        client.get(f"/api/sessions/{session_id}/messages/page?before=other-msg").status_code
        == 400
    )
    assert client.get("/api/sessions/missing/messages/page").status_code == 404
    assert client.get(f"/api/sessions/{session_id}/messages/page?limit=0").status_code == 422
