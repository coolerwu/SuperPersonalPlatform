from __future__ import annotations

from app.main import create_app

SNAPSHOT = {
    "slash_commands": ["compact"],
    "tools": [
        "Read",
        "Bash",
        "mcp__platform_tools__backtest_run",
        "mcp__playwright__browser_open",
    ],
    "mcp_servers": [
        {"name": "platform_tools", "status": "connected", "source": "sdk"},
        {"name": "playwright", "status": "failed", "source": "plugin"},
    ],
    "plugins": [{"name": "playwright", "version": "1.0.0"}],
    "plugin_errors": [
        {
            "plugin": "bad@market",
            "type": "manifest-validation-error",
            "message": "boom",
        }
    ],
    "skills": ["z-skill", "quant-backtest"],
    "claude_code_version": "2.1.321",
    "model": "deepseek",
    "permission_mode": "bypassPermissions",
}


def _install_runner(client, snapshot):
    class Runner:
        def runtime_snapshot(self, agent_id):
            return snapshot if agent_id == "default" else None

    client.app.state.runner = Runner()


def test_tools_api_requires_auth() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/tools").status_code == 401


def test_tools_without_runtime_is_not_loaded(client) -> None:
    body = client.get("/api/tools", params={"agent_id": "default"}).json()
    assert body["loaded"] is False
    assert body["claude_tools"] == []
    assert body["mcp_tools"] == []
    assert "发一条消息" in body["note"]


def test_tools_read_the_runtime_snapshot(client) -> None:
    _install_runner(client, SNAPSHOT)
    body = client.get("/api/tools", params={"agent_id": "default"}).json()

    assert body["loaded"] is True
    assert body["agent_id"] == "default"
    claude = {tool["name"]: tool for tool in body["claude_tools"]}
    assert set(claude) == {"Read", "Bash"}
    assert all(tool["source"] == "claude-code" for tool in claude.values())

    mcp = {(tool["server"], tool["name"]): tool for tool in body["mcp_tools"]}
    assert set(mcp) == {
        ("platform_tools", "backtest_run"),
        ("playwright", "browser_open"),
    }
    backtest = mcp[("platform_tools", "backtest_run")]
    assert backtest["raw_name"] == "mcp__platform_tools__backtest_run"
    assert backtest["source"] == "mcp"
    assert backtest["description"]
    assert mcp[("playwright", "browser_open")]["description"] == ""

    servers = {server["name"]: server for server in body["mcp_servers"]}
    assert servers["platform_tools"]["status"] == "connected"
    assert servers["platform_tools"]["source"] == "sdk"
    assert servers["playwright"]["status"] == "failed"

    assert body["skills"] == ["quant-backtest", "z-skill"]
    assert body["plugins"] == [{"name": "playwright", "version": "1.0.0"}]
    assert body["plugin_errors"] == SNAPSHOT["plugin_errors"]
    assert body["claude_code_version"] == "2.1.321"
    assert body["model"] == "deepseek"
    assert body["permission_mode"] == "bypassPermissions"
    assert "init frame" in body["note"]


def test_tools_unknown_agent_is_not_loaded(client) -> None:
    _install_runner(client, SNAPSHOT)
    body = client.get("/api/tools", params={"agent_id": "other"}).json()
    assert body["loaded"] is False
    assert body["mcp_tools"] == []
