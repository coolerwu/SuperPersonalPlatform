from __future__ import annotations

from app.main import create_app


def test_tools_api_requires_auth() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/tools").status_code == 401


def test_tools_api_declares_claude_code_and_unwired_tools(client) -> None:
    body = client.get("/api/tools").json()

    filesystem = {tool["name"]: tool for tool in body["filesystem_tools"]}
    known = {
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Task",
        "WebFetch",
        "WebSearch",
        "Skill",
        "ToolSearch",
        "DesignSync",
    }
    assert known <= set(filesystem)
    names = [tool["name"] for tool in body["filesystem_tools"]]
    assert len(names) == len(set(names))
    assert all(tool["source"] == "claude-code" for tool in filesystem.values())
    assert all(tool["wired"] and tool["registered"] for tool in filesystem.values())
    assert "随 bundled CLI 版本" in body["claude_code_note"]

    platform = {tool["name"]: tool for tool in body["platform_tools"]}
    assert set(platform) == {"skill_save"}
    assert platform["skill_save"]["source"] == "platform"
    assert platform["skill_save"]["wired"] is False
    assert platform["skill_save"]["registered"] is False

    skills = {tool["name"]: tool for tool in body["skill_tools"]}
    for name in (
        "web_search",
        "web_fetch",
        "backtest_run",
        "browser_open",
        "chan_analyze",
        "trade_journal_record",
        "allocation_overview",
    ):
        assert skills[name]["registered"] is True, name
        assert skills[name]["source"] == "skill"
        # Registry code is kept, but no skill tool is wired to the SDK yet.
        assert skills[name]["wired"] is False
    # The web-search/web-fetch skills were retired in favour of Claude Code's
    # built-in WebFetch/WebSearch; their tool modules stay registered for the
    # tool-layer migration but no skill declares them any more.
    assert skills["web_search"]["skills"] == []
    assert skills["web_fetch"]["skills"] == []
    # Cron tools have no module and no skill declares them.
    assert "cron_list" not in skills
