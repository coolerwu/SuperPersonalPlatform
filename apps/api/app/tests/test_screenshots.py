import base64
import shutil

from app.config import settings

RUN = "b" * 32
SHOT = "c" * 32
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)


def _new_session(client) -> tuple[str, str]:
    agents = client.get("/api/agents").json()
    assert agents
    agent_id = agents[0]["id"]
    session = client.post(
        f"/api/agents/{agent_id}/sessions", json={"title": "screenshot"}
    ).json()
    return agent_id, session["id"]


def _write_screenshot(agent_id: str, session_id: str, run_id: str = RUN, shot_id: str = SHOT):
    path = (
        settings.workspaces_dir
        / agent_id
        / "screenshots"
        / session_id
        / run_id
        / f"{shot_id}.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def _drop_screenshots(agent_id: str, session_id: str) -> None:
    shutil.rmtree(
        settings.workspaces_dir / agent_id / "screenshots" / session_id,
        ignore_errors=True,
    )


def test_list_and_get_screenshot(client) -> None:
    agent_id, session_id = _new_session(client)
    _write_screenshot(agent_id, session_id)
    try:
        response = client.get(f"/api/sessions/{session_id}/screenshots")
        assert response.status_code == 200
        rows = response.json()
        assert [(row["id"], row["run_id"]) for row in rows] == [(SHOT, RUN)]

        response = client.get(
            f"/api/sessions/{session_id}/screenshots/{RUN}/{SHOT}.png"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == PNG
    finally:
        _drop_screenshots(agent_id, session_id)
        client.delete(f"/api/sessions/{session_id}")


def test_invalid_screenshot_ids_are_404(client) -> None:
    assert client.get("/api/sessions/not-a-session/screenshots").status_code == 404
    missing = "d" * 32
    assert client.get(f"/api/sessions/{missing}/screenshots").status_code == 404

    agent_id, session_id = _new_session(client)
    try:
        assert (
            client.get(
                f"/api/sessions/{session_id}/screenshots/{RUN}/short.png"
            ).status_code
            == 404
        )
    finally:
        client.delete(f"/api/sessions/{session_id}")


def test_deleting_session_removes_screenshots(client) -> None:
    agent_id, session_id = _new_session(client)
    _write_screenshot(agent_id, session_id)
    try:
        response = client.delete(f"/api/sessions/{session_id}")
        assert response.status_code == 200
        assert not (
            settings.workspaces_dir / agent_id / "screenshots" / session_id
        ).exists()
    finally:
        _drop_screenshots(agent_id, session_id)
