from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import StructuredTool

from app.services.skill_middleware import AgentSkillsMiddleware
from app.services.skill_tools import (
    SkillToolContext,
    SkillToolRegistry,
    normalize_tool_names,
    tool_endpoint,
)


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(lambda value: value, name=name, description=f"{name} tool")


def _context() -> SkillToolContext:
    return SkillToolContext(api_key="test-key", base_url="https://api.deepseek.com")


def _write_skill(root: Path, directory: str, name: str) -> None:
    target = root / directory
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n", encoding="utf-8"
    )


def test_builtin_registry_has_web_tools() -> None:
    import app.services.browser_tools  # noqa: F401 - registers browser tools
    import app.services.memory_tools  # noqa: F401 - registers memory tools
    from app.services.browser_tools import BROWSER_TOOL_NAMES
    from app.services.skill_tools import SKILL_TOOL_REGISTRY

    assert {"web_fetch", "web_search"} | BROWSER_TOOL_NAMES <= {
        spec.name for spec in SKILL_TOOL_REGISTRY.specs()
    }
    assert {"memory_list", "memory_update", "memory_delete"} <= {
        spec.name for spec in SKILL_TOOL_REGISTRY.specs()
    }


def test_registry_builds_registered_tools() -> None:
    registry = SkillToolRegistry([_tool("alpha")])
    assert [spec.name for spec in registry.specs()] == ["alpha"]
    assert [tool.name for tool in registry.build(_context())] == ["alpha"]
    assert [tool.name for tool in registry.resolve_names(["alpha"], _context())] == ["alpha"]
    assert registry.resolve_names(["missing"], _context()) == []


def test_registry_rejects_reserved_duplicate_and_invalid_names() -> None:
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("read_file")])
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("alpha"), _tool("alpha")])
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("bad name")])


def test_normalize_tool_names_dedupes_and_validates() -> None:
    assert normalize_tool_names([" web_fetch ", "web_fetch", "alpha"]) == ["web_fetch", "alpha"]
    with pytest.raises(ValueError):
        normalize_tool_names([""])
    with pytest.raises(ValueError):
        normalize_tool_names(["bad name"])


def test_tool_endpoint_joins_base_url() -> None:
    assert tool_endpoint("https://api.deepseek.com") == "https://api.deepseek.com/v1/tools/fetch"
    assert tool_endpoint("https://api.deepseek.com/") == "https://api.deepseek.com/v1/tools/fetch"
    assert tool_endpoint("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/tools/fetch"


def _middleware(tmp_path: Path, enabled: set[str]) -> AgentSkillsMiddleware:
    from deepagents.backends import FilesystemBackend

    _write_skill(tmp_path, "alpha", "alpha")
    _write_skill(tmp_path, "beta", "beta")
    return AgentSkillsMiddleware(
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        sources=[("/", "Test")],
        enabled_skills=enabled,
        tools=[],
    )


def test_middleware_occupies_builtin_slot(tmp_path: Path) -> None:
    middleware = _middleware(tmp_path, {"alpha"})
    assert middleware.name == "SkillsMiddleware"


def test_middleware_filters_enabled_skills(tmp_path: Path) -> None:
    middleware = _middleware(tmp_path, {"alpha"})
    update = middleware.before_agent({"skills_metadata": None}, None, {})
    assert update is not None
    assert [skill["name"] for skill in update["skills_metadata"]] == ["alpha"]


async def test_middleware_filters_enabled_skills_async(tmp_path: Path) -> None:
    middleware = _middleware(tmp_path, {"beta"})
    update = await middleware.abefore_agent({"skills_metadata": None}, None, {})
    assert update is not None
    assert [skill["name"] for skill in update["skills_metadata"]] == ["beta"]


def test_middleware_refilters_already_loaded_state(tmp_path: Path) -> None:
    middleware = _middleware(tmp_path, {"alpha"})
    loaded = [
        {"name": "alpha", "description": "a", "path": "/skills/alpha/SKILL.md"},
        {"name": "beta", "description": "b", "path": "/skills/beta/SKILL.md"},
    ]
    update = middleware.before_agent({"skills_metadata": loaded}, None, {})
    assert update is not None
    assert [skill["name"] for skill in update["skills_metadata"]] == ["alpha"]


def test_middleware_skips_prompt_without_visible_skills(tmp_path: Path) -> None:
    middleware = _middleware(tmp_path, set())
    request = ModelRequest(
        model=SimpleNamespace(),
        messages=[],
        tools=[],
        state={"skills_metadata": []},
    )
    assert middleware.modify_request(request) is request
