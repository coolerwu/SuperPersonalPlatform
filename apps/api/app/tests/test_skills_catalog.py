from __future__ import annotations

import os
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
    assert skill.include_tools == ("alpha", "beta")
    assert any("space-separated" in warning for warning in skill.warnings)


def test_normalize_skill_names_dedupes_and_validates() -> None:
    assert normalize_skill_names([" demo ", "demo", "alpha"]) == ["demo", "alpha"]
    assert normalize_skill_names(["café", "café", "alpha"]) == ["café", "alpha"]
    assert is_valid_skill_name("demo-skill-2") is True
    for invalid in ("", "Demo", "-demo", "demo-", "a--b", "a b", "a" * 65):
        assert is_valid_skill_name(invalid) is False
        with pytest.raises(ValueError):
            normalize_skill_names([invalid])


def test_catalog_and_middleware_agree_on_names(tmp_path: Path) -> None:
    from deepagents.backends import FilesystemBackend

    from app.services.skill_middleware import AgentSkillsMiddleware

    _write_skill(tmp_path, "alpha", "name: alpha\ndescription: Alpha")
    _write_skill(tmp_path, "beta", "name: beta\ndescription: Beta")

    catalog = scan_skills(tmp_path)
    middleware = AgentSkillsMiddleware(
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        sources=[("/", "Test")],
        enabled_skills=[skill.name for skill in catalog.skills],
        tools=[],
    )
    update = middleware.before_agent({"skills_metadata": None}, None, {})
    assert update is not None
    loaded = {skill["name"] for skill in update["skills_metadata"]}
    assert loaded == {skill.name for skill in catalog.skills}


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
