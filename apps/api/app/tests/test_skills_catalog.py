from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.skills import is_valid_skill_name, normalize_skill_names, scan_skills


def _write_skill(root: Path, directory: str, frontmatter: str, body: str = "body") -> Path:
    target = root / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return target


def test_scan_skills_parses_frontmatter(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "demo",
        "name: demo\ndescription: Demo skill\nmetadata:\n  include_tools: alpha beta",
    )

    catalog = scan_skills(tmp_path)
    assert catalog.errors == ()
    assert len(catalog.skills) == 1
    skill = catalog.skills[0]
    assert skill.name == "demo"
    assert skill.description == "Demo skill"
    assert skill.path == "/skills/demo/SKILL.md"
    assert skill.directory == "demo"
    assert skill.include_tools == ("alpha", "beta")
    assert skill.can_enable is True
    assert skill.warnings == ()
    assert skill.source == "manual"


def test_scan_skills_missing_root_returns_empty(tmp_path: Path) -> None:
    catalog = scan_skills(tmp_path / "missing")
    assert catalog.skills == ()
    assert catalog.errors == ()


def test_scan_skills_reports_invalid_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    missing_description = tmp_path / "missing-description"
    missing_description.mkdir()
    (missing_description / "SKILL.md").write_text("---\nname: missing-description\n---\n", encoding="utf-8")

    catalog = scan_skills(tmp_path)
    assert catalog.skills == ()
    assert len(catalog.errors) == 2


def test_scan_skills_warns_on_name_mismatch_but_keeps_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo-dir", "name: demo-name\ndescription: Mismatch")

    catalog = scan_skills(tmp_path)
    assert len(catalog.skills) == 1
    skill = catalog.skills[0]
    assert skill.name == "demo-name"
    assert skill.path == "/skills/demo-dir/SKILL.md"
    assert skill.can_enable is True
    assert any("does not match directory" in warning for warning in skill.warnings)


def test_scan_skills_marks_invalid_names_unenableable(tmp_path: Path) -> None:
    _write_skill(tmp_path, "bad_name", "name: Bad_Name\ndescription: Invalid")

    catalog = scan_skills(tmp_path)
    assert len(catalog.skills) == 1
    assert catalog.skills[0].can_enable is False


def test_scan_skills_ignores_plain_files_and_hidden_dirs(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("not a skill", encoding="utf-8")
    _write_skill(tmp_path / ".hidden", "demo", "name: demo\ndescription: Hidden")

    catalog = scan_skills(tmp_path)
    assert catalog.skills == ()


def test_scan_skills_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write_skill(outside, "escape", "name: escape\ndescription: Outside")
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside / "escape", root / "escape")

    catalog = scan_skills(root)
    assert catalog.skills == ()
    assert any("outside the skills root" in error for error in catalog.errors)


def test_scan_skills_warns_when_include_tools_is_a_list(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "listed",
        "name: listed\ndescription: List form\nmetadata:\n  include_tools:\n    - alpha\n    - beta",
    )

    catalog = scan_skills(tmp_path)
    skill = catalog.skills[0]
    assert skill.include_tools == ()
    assert any("list form is ignored" in warning for warning in skill.warnings)


def test_normalize_skill_names_dedupes_and_validates() -> None:
    assert normalize_skill_names([" demo ", "demo", "alpha"]) == ["demo", "alpha"]
    assert normalize_skill_names(["café", "café", "alpha"]) == ["café", "alpha"]
    assert is_valid_skill_name("demo-skill-2") is True
    for invalid in ("", "Demo", "-demo", "demo-", "a--b", "a b", "a" * 65):
        assert is_valid_skill_name(invalid) is False
        with pytest.raises(ValueError):
            normalize_skill_names([invalid])


def test_seeded_skills_do_not_declare_platform_tools() -> None:
    from app.services import (  # noqa: F401 - import registers tool factories
        allocation_tools,
        backtest_tools,
        browser_tools,
        chan_tools,
        skill_tools,
        trade_journal_tools,
    )
    from app.services.skill_tools import SKILL_TOOL_REGISTRY
    from app.services.skills import seed_default_skills

    seed_default_skills()
    catalog = scan_skills()
    declared = {tool for skill in catalog.skills for tool in skill.include_tools}
    registered = {spec.name for spec in SKILL_TOOL_REGISTRY.specs()}

    # Native Claude Code skills do not declare platform tools; availability is
    # decided by which MCP tools the runner mounts.
    assert declared == set()
    assert {
        "web_search",
        "web_fetch",
        "backtest_run",
        "browser_open",
        "chan_analyze",
        "trade_journal_record",
        "allocation_overview",
    } <= registered
    # Cron tools lost their module with the legacy stack.
    assert "cron_list" not in registered
    assert "cron_run" not in registered
    assert "memory-organizer" not in {skill.name for skill in catalog.skills}


def test_default_skills_seeded(client) -> None:
    body = client.get("/api/skills").json()
    skills = {item["name"]: item for item in body["skills"]}
    assert (settings.skills_dir / "quant-backtest" / "SKILL.md").is_file()
    assert skills["quant-backtest"]["include_tools"] == []
    assert skills["quant-backtest"]["source"] == "initial"
    # The knowledge-base skill was retired; the mirror and its sync runtime
    # stay, and Agents use plain file tools on it.
    assert "knowledge-base" not in skills
    # Web search/fetch moved to Claude Code's built-in WebSearch/WebFetch;
    # browser automation comes from the official Playwright plugin.
    assert "web-search" not in skills
    assert "web-fetch" not in skills
    assert "web-browser" not in skills
    assert "memory-organizer" not in skills


def test_skills_api_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/skills").status_code == 401


def test_skills_api_returns_catalog(client) -> None:
    _write_skill(
        settings.skills_dir,
        "api-skill",
        "name: api-skill\ndescription: API skill\nmetadata:\n  include_tools: alpha",
    )
    broken = settings.skills_dir / "api-broken"
    broken.mkdir(exist_ok=True)
    (broken / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")

    response = client.get("/api/skills")
    assert response.status_code == 200
    body = response.json()
    skill = next(item for item in body["skills"] if item["name"] == "api-skill")
    assert skill["description"] == "API skill"
    assert skill["path"] == "/skills/api-skill/SKILL.md"
    assert skill["include_tools"] == ["alpha"]
    assert skill["can_enable"] is True
    assert any("api-broken" in error for error in body["errors"])


def test_delete_initialized_skill_keeps_tombstone(client) -> None:
    response = client.delete("/api/skills/chanlun")
    assert response.status_code == 200
    assert not (settings.skills_dir / "chanlun").exists()

    from app.services.skills import seed_default_skills

    seed_default_skills()
    assert not (settings.skills_dir / "chanlun").exists()


def test_modified_flag_marks_edited_initial_skill(client) -> None:
    target = settings.skills_dir / "quant-backtest" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    if not original.endswith("\nagent edit\n"):
        target.write_text(original + "\nagent edit\n", encoding="utf-8")

    listed = client.get("/api/skills").json()
    skill = next(item for item in listed["skills"] if item["name"] == "quant-backtest")
    assert skill["source"] == "initial"
    assert skill["modified"] is True

    detail = client.get("/api/skills/quant-backtest").json()
    assert detail["modified"] is True

    untouched = next(item for item in listed["skills"] if item["name"] == "trade-journal")
    assert untouched["modified"] is False


def test_initialization_skills_reference_only_mounted_tools() -> None:
    from app.services.platform_mcp import BUSINESS_TOOL_NAMES
    from app.services.skills import load_seed_skills

    pattern = re.compile(
        r"\b(?:backtest|market|trade_journal|allocation|chan)_[a-z_]+\b"
    )
    referenced = set()
    for content in load_seed_skills().values():
        referenced |= set(pattern.findall(content))
        assert "## 工具清单" in content
        assert "## 组合方式" in content
        assert "## 硬契约" in content

    assert referenced
    assert referenced <= set(BUSINESS_TOOL_NAMES), referenced - set(BUSINESS_TOOL_NAMES)
