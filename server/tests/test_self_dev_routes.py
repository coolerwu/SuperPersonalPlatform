from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.self_dev_routes import create_self_dev_router
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.self_dev_service import SelfDevService
from server.app.job_service import JobService
from server.domain.auth import AuthToken
from server.infrastructure.session import SessionCodec


def write_config(workspace: Path) -> None:
    (workspace / "config.yaml").write_text(
        "auth:\n  token: secret-token\nproxy:\n  upstream_base_url: http://example.test/\n",
        encoding="utf-8",
    )


def make_client(workspace: Path) -> TestClient:
    config_service = ConfigFileService(workspace)
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=config_service,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        session_codec=SessionCodec("secret-token"),
        self_dev_service=SelfDevService(workspace, None),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_self_dev_router(container))
    return TestClient(app)


def test_self_dev_tasks_require_authentication(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)

    response = client.get("/api/self-dev/tasks")

    assert response.status_code == 401


def test_self_dev_task_create_list_and_read(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    create_response = client.post(
        "/api/self-dev/tasks",
        json={
            "goal": "update README",
            "agent_id": "assistant",
            "repo_url": "https://github.com/coolerwu/SuperPersonalPlatform.git",
        },
    )

    assert create_response.status_code == 200
    task = create_response.json()["task"]
    assert task["status"] == "created"
    assert task["branch"].startswith("agent/self-dev-")
    assert (tmp_path / "self-dev" / "tasks" / task["id"] / "task.json").exists()

    list_response = client.get("/api/self-dev/tasks")
    read_response = client.get(f"/api/self-dev/tasks/{task['id']}")

    assert list_response.status_code == 200
    assert list_response.json()["tasks"][0]["id"] == task["id"]
    assert read_response.status_code == 200
    assert read_response.json()["task"]["events"][0]["type"] == "created"


def test_self_dev_run_task_enqueues_durable_job(tmp_path) -> None:
    write_config(tmp_path)
    service = SelfDevService(tmp_path, object())
    task = service.create_task("update README", "assistant")

    result = __import__("asyncio").run(service.run_task(task.id, instruction="continue"))

    assert result.status == "queued"
    raw = __import__("json").loads(
        (tmp_path / "self-dev" / "tasks" / task.id / "task.json").read_text(encoding="utf-8")
    )
    assert raw["job"]["type"] == "self_dev.run_task"
    assert raw["job"]["payload"] == {"allow_push": False, "instruction": "continue"}


def test_self_dev_cancel_task_marks_job_cancelled(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})
    task = client.post(
        "/api/self-dev/tasks",
        json={"goal": "update README", "agent_id": "assistant"},
    ).json()["task"]
    JobService(tmp_path).enqueue(task["id"], "self_dev.run_task", {"instruction": "continue"})

    response = client.post(
        f"/api/self-dev/tasks/{task['id']}/cancel",
        json={"reason": "not needed"},
    )

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "cancelled"


def test_self_dev_cancel_task_marks_stale_running_task_cancelled(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})
    task = client.post(
        "/api/self-dev/tasks",
        json={"goal": "update README", "agent_id": "assistant"},
    ).json()["task"]
    service = SelfDevService(tmp_path, None)
    service._replace_task(service._read_task(task["id"]), status="running")

    response = client.post(
        f"/api/self-dev/tasks/{task['id']}/cancel",
        json={"reason": "stale running task"},
    )

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "cancelled"


def test_self_dev_accept_reject_require_review_status(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})
    task = client.post(
        "/api/self-dev/tasks",
        json={"goal": "update README", "agent_id": "assistant"},
    ).json()["task"]

    accept_response = client.post(
        f"/api/self-dev/tasks/{task['id']}/accept",
        json={"note": "ok"},
    )
    reject_response = client.post(
        f"/api/self-dev/tasks/{task['id']}/reject",
        json={"reason": "revise"},
    )

    assert accept_response.status_code == 400
    assert reject_response.status_code == 400


def test_self_dev_accept_reports_push_failure(tmp_path) -> None:
    write_config(tmp_path)
    service = SelfDevService(tmp_path, None)
    task = service.create_task("update README", "assistant")
    repo = tmp_path / "repo"
    repo.mkdir()
    task = service._replace_task(
        task,
        status="needs_review",
        recommendation="push",
        repo_path=str(repo),
    )

    calls = []

    def fake_git_result(args, cwd):
        calls.append(args)
        if args[0] == "push":
            return {"command": "git push origin HEAD", "returncode": 128, "output": "Authentication failed"}
        return {"command": "git " + " ".join(args), "returncode": 0, "output": ""}

    service._git_result = fake_git_result

    result = __import__("asyncio").run(service.accept_task(task.id, "accept"))

    assert result.status == "failed"
    assert "Authentication failed" in result.error
    assert ["push", "origin", "HEAD"] in calls
