from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from app.config import settings
from app.main import create_app
from app.services.skill_tools import SkillToolRegistry


def _write_skill(directory: str, frontmatter: str) -> None:
    target = settings.skills_dir / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nbody\n", encoding="utf-8")


def test_tools_api_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/tools").status_code == 401


def test_tools_api_includes_builtin_web_tools(client) -> None:
    body = client.get("/api/tools").json()
    tools = {tool["name"]: tool for tool in body["skill_tools"]}
    assert tools["web_fetch"]["registered"] is True
    assert tools["web_search"]["registered"] is True
    assert tools["web_fetch"]["skills"] == ["web-fetch"]
    assert tools["web_search"]["skills"] == ["web-search"]
    for name in (
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_screenshot",
        "browser_close",
    ):
        assert tools[name]["registered"] is True
        assert tools[name]["skills"] == ["web-browser"]
    for name in ("memory_list", "memory_update", "memory_delete"):
        assert tools[name]["registered"] is True
        assert tools[name]["skills"] == ["memory-organizer"]


def test_tools_api_reports_filesystem_and_skill_tools(client, monkeypatch) -> None:
    import app.routers.tools as tools_router

    _write_skill(
        "tools-api-skill",
        "name: tools-api-skill\ndescription: Tools API\nmetadata:\n"
        "  include_tools: demo_tool missing_tool",
    )
    fake = StructuredTool.from_function(
        lambda value: value, name="demo_tool", description="Demo tool"
    )
    monkeypatch.setattr(tools_router, "SKILL_TOOL_REGISTRY", SkillToolRegistry([fake]))

    body = client.get("/api/tools").json()
    filesystem = {tool["name"]: tool for tool in body["filesystem_tools"]}
    assert {"read_file", "write_file", "grep"} <= set(filesystem)
    assert filesystem["read_file"]["registered"] is True

    skill_tools = {tool["name"]: tool for tool in body["skill_tools"]}
    assert skill_tools["demo_tool"]["registered"] is True
    assert skill_tools["demo_tool"]["description"] == "Demo tool"
    assert skill_tools["demo_tool"]["skills"] == ["tools-api-skill"]
    assert skill_tools["missing_tool"]["registered"] is False
    assert skill_tools["missing_tool"]["skills"] == ["tools-api-skill"]
