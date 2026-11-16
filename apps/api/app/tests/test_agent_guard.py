import asyncio
import json
from pathlib import Path

from app.services import agent_guard


def _call(guard, tool: str, tool_input: dict):
    return asyncio.run(
        guard(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "tool_input": tool_input,
                "tool_use_id": "tool-1",
            },
            "tool-1",
            {},
        )
    )


def _denied(output: dict) -> bool:
    return (
        output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspaces" / "agent-1"
    workspace.mkdir(parents=True)
    return workspace


def test_file_tools_inside_workspace_are_allowed(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "notes.md").write_text("hello", "utf-8")
    guard = agent_guard.workspace_guard(workspace)

    assert not _denied(_call(guard, "Read", {"file_path": "notes.md"}))
    assert not _denied(_call(guard, "Read", {"file_path": str(workspace / "notes.md")}))
    assert not _denied(_call(guard, "Write", {"file_path": "out.txt"}))
    assert not _denied(_call(guard, "Edit", {"file_path": "notes.md"}))
    assert not _denied(_call(guard, "MultiEdit", {"file_path": "notes.md"}))
    assert not _denied(_call(guard, "NotebookEdit", {"notebook_path": "book.ipynb"}))
    assert not _denied(_call(guard, "Glob", {"pattern": "**/*.md"}))
    assert not _denied(_call(guard, "Grep", {"pattern": "hello"}))


def test_file_tools_outside_workspace_are_denied(tmp_path):
    workspace = _workspace(tmp_path)
    outside = tmp_path / "secrets" / "token.txt"
    outside.parent.mkdir()
    outside.write_text("secret", "utf-8")
    guard = agent_guard.workspace_guard(workspace)

    assert _denied(_call(guard, "Read", {"file_path": str(outside)}))
    assert _denied(_call(guard, "Read", {"file_path": "../secrets/token.txt"}))
    assert _denied(_call(guard, "Write", {"file_path": "/etc/passwd"}))
    assert _denied(_call(guard, "Glob", {"path": str(tmp_path)}))
    assert _denied(_call(guard, "Grep", {"path": ".."}))
    assert _denied(_call(guard, "Glob", {"pattern": f"{tmp_path}/**"}))
    assert _denied(_call(guard, "Glob", {"pattern": "../**"}))
    assert _denied(_call(guard, "Glob", {"pattern": "**/../../**"}))


def test_symlink_escape_is_denied(tmp_path):
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", "utf-8")
    (workspace / "link.txt").symlink_to(outside)
    guard = agent_guard.workspace_guard(workspace)

    assert _denied(_call(guard, "Read", {"file_path": "link.txt"}))


def test_missing_or_malformed_paths_leave_the_decision_to_the_tool(tmp_path):
    guard = agent_guard.workspace_guard(_workspace(tmp_path))

    assert _call(guard, "Read", {}) == {}
    assert _call(guard, "Read", {"file_path": ""}) == {}
    assert _call(guard, "Read", {"file_path": 42}) == {}
    assert _call(guard, "Glob", {"pattern": "*.md"}) == {}


def test_non_file_tools_get_no_hook_opinion(tmp_path):
    guard = agent_guard.workspace_guard(_workspace(tmp_path))

    assert _call(guard, "Bash", {"command": "cat /etc/passwd"}) == {}
    assert _call(guard, "Task", {"description": "x", "prompt": "y"}) == {}
    assert _call(guard, "mcp__platform_tools__backtest_run", {}) == {}
    assert _call(guard, "WebSearch", {"query": "x"}) == {}


def test_webfetch_rejects_local_and_private_hosts(tmp_path):
    guard = agent_guard.workspace_guard(_workspace(tmp_path))

    assert _denied(_call(guard, "WebFetch", {"url": "http://localhost:8888/api/health"}))
    assert _denied(_call(guard, "WebFetch", {"url": "http://127.0.0.1/x"}))
    assert _denied(_call(guard, "WebFetch", {"url": "http://[::1]/x"}))
    assert _denied(_call(guard, "WebFetch", {"url": "http://192.168.1.3/x"}))
    assert _denied(_call(guard, "WebFetch", {"url": "http://10.0.0.5/x"}))
    assert _denied(_call(guard, "WebFetch", {"url": "ftp://example.com/x"}))
    assert not _denied(_call(guard, "WebFetch", {"url": "https://example.com/page"}))
    assert _call(guard, "WebFetch", {}) == {}


def test_malformed_hook_input_is_denied(tmp_path):
    guard = agent_guard.workspace_guard(_workspace(tmp_path))

    assert _denied(asyncio.run(guard("not-a-dict", None, {})))


def test_restricted_allowed_tools_include_whitelist_and_mcp(tmp_path):
    tools = agent_guard.build_restricted_allowed_tools(
        maps_enabled=True, plugin_servers=["playwright"]
    )

    for name in ("Read", "Bash", "Task", "WebSearch", "Skill"):
        assert name in tools
    assert "mcp__platform_tools__backtest_run" in tools
    assert "mcp__amap" in tools
    assert "mcp__playwright" in tools

    without = agent_guard.build_restricted_allowed_tools(maps_enabled=False)
    assert "mcp__amap" not in without


def test_installed_mcp_server_names_reads_plugin_manifests(tmp_path):
    claude = tmp_path / "claude"
    plugin_dir = claude / "plugins" / "cache" / "market" / "playwright" / "1.0.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / ".mcp.json").write_text(
        json.dumps({"playwright": {"command": "npx"}}), "utf-8"
    )
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "playwright@market": [{"installPath": str(plugin_dir)}],
                    "broken@market": [{"installPath": str(tmp_path / "missing")}],
                },
            }
        ),
        "utf-8",
    )

    assert agent_guard.installed_mcp_server_names(tmp_path) == ["playwright"]


def test_installed_mcp_server_names_tolerates_missing_or_bad_metadata(tmp_path):
    assert agent_guard.installed_mcp_server_names(tmp_path) == []

    meta = tmp_path / "claude" / "plugins" / "installed_plugins.json"
    meta.parent.mkdir(parents=True)
    meta.write_text("not json", "utf-8")
    assert agent_guard.installed_mcp_server_names(tmp_path) == []
