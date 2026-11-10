from __future__ import annotations

from app.main import create_app


def test_tools_api_requires_auth() -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/tools").status_code == 401


def test_tools_api_declares_claude_code_and_unwired_tools(client) -> None:
    body = client.get("/api/tools").json()

    filesystem = {tool["name"]: tool for tool in body["filesystem_tools"]}
    assert set(filesystem) == {
        "Task",
        "Bash",
        "CronCreate",
        "CronDelete",
        "CronList",
        "Edit",
        "EnterWorktree",
        "ExitWorktree",
        "ListAgents",
        "Monitor",
        "NotebookEdit",
        "Read",
        "ReportFindings",
        "ScheduleWakeup",
        "SendMessage",
        "Skill",
        "TaskStop",
        "WebFetch",
        "WebSearch",
        "Workflow",
        "Write",
    }
    assert all(tool["source"] == "claude-code" for tool in filesystem.values())
    assert all(tool["wired"] and tool["registered"] for tool in filesystem.values())

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
    assert skills["web_search"]["skills"] == ["web-search"]

    # cron tools lost their module in the legacy-stack removal; the skill still
    # declares them until the tool-layer migration rewrites the contract.
    for name in (
        "cron_list",
        "cron_targets",
        "cron_create",
        "cron_update",
        "cron_delete",
        "cron_run",
    ):
        assert skills[name]["registered"] is False, name
        assert skills[name]["skills"] == ["cron-scheduler"]
