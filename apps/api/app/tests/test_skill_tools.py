from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import StructuredTool

from app.services.skill_middleware import AgentSkillsMiddleware
from app.services.skill_tools import SkillToolRegistry


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(lambda value: value, name=name, description=f"{name} tool")


def _write_skill(root: Path, directory: str, name: str) -> None:
    target = root / directory
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n", encoding="utf-8"
    )


def test_registry_resolves_registered_tools() -> None:
    registry = SkillToolRegistry([_tool("alpha")])
    assert [tool.name for tool in registry.tools()] == ["alpha"]
    assert [tool.name for tool in registry.resolve("alpha")] == ["alpha"]
    assert registry.resolve("missing") == []


def test_registry_rejects_reserved_duplicate_and_invalid_names() -> None:
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("read_file")])
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("alpha"), _tool("alpha")])
    with pytest.raises(ValueError):
        SkillToolRegistry([_tool("bad name")])


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
