from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.skills import (
    SEED_STATE_FILENAME,
    load_seed_skills,
    scan_skills,
    seed_default_skills,
)

TEMPLATES = {
    "demo-skill": "---\nname: demo-skill\ndescription: Demo\n---\n\n# demo\n",
    "other-skill": "---\nname: other-skill\ndescription: Other\n---\n\n# other\n",
}


def _state(root: Path) -> dict:
    return json.loads((root / SEED_STATE_FILENAME).read_text(encoding="utf-8"))


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_seed_creates_missing_skills_and_state(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)

    for name, content in TEMPLATES.items():
        assert (tmp_path / name / "SKILL.md").read_text(encoding="utf-8") == content
    state = _state(tmp_path)
    assert state["version"] == 1
    assert state["skills"] == {name: _hash(content) for name, content in TEMPLATES.items()}
    assert {skill.name for skill in scan_skills(tmp_path).skills} == set(TEMPLATES)


def test_seed_updates_unmodified_skill(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    updated = {**TEMPLATES, "demo-skill": TEMPLATES["demo-skill"] + "\nupdated\n"}

    seed_default_skills(tmp_path, updated)

    assert (
        tmp_path / "demo-skill" / "SKILL.md"
    ).read_text(encoding="utf-8") == updated["demo-skill"]
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(updated["demo-skill"])


def test_seed_preserves_edited_skill(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    target = tmp_path / "demo-skill" / "SKILL.md"
    target.write_text("user customized content", encoding="utf-8")
    updated = {**TEMPLATES, "demo-skill": "new initialization content\n"}

    seed_default_skills(tmp_path, updated)

    assert target.read_text(encoding="utf-8") == "user customized content"
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])


def test_seed_does_not_resurrect_deleted_skill(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    (tmp_path / "demo-skill" / "SKILL.md").unlink()

    seed_default_skills(tmp_path, TEMPLATES)

    assert not (tmp_path / "demo-skill" / "SKILL.md").exists()
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])


def test_seed_adopts_existing_file_that_matches_template(tmp_path: Path) -> None:
    target = tmp_path / "demo-skill" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(TEMPLATES["demo-skill"], encoding="utf-8")

    seed_default_skills(tmp_path, {"demo-skill": TEMPLATES["demo-skill"]})
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])

    updated = {**TEMPLATES, "demo-skill": TEMPLATES["demo-skill"] + "\nnext\n"}
    seed_default_skills(tmp_path, updated)
    assert target.read_text(encoding="utf-8") == updated["demo-skill"]


def test_seed_leaves_untracked_skill_that_differs_from_template(tmp_path: Path) -> None:
    target = tmp_path / "demo-skill" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("my own version\n", encoding="utf-8")

    seed_default_skills(tmp_path, {"demo-skill": TEMPLATES["demo-skill"]})

    assert target.read_text(encoding="utf-8") == "my own version\n"
    assert not (tmp_path / SEED_STATE_FILENAME).exists()


def test_seed_drops_retired_tracking_but_keeps_files(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)

    seed_default_skills(tmp_path, {"demo-skill": TEMPLATES["demo-skill"]})

    retired = tmp_path / "other-skill" / "SKILL.md"
    assert retired.read_text(encoding="utf-8") == TEMPLATES["other-skill"]
    assert "other-skill" not in _state(tmp_path)["skills"]

    seed_default_skills(tmp_path, {"demo-skill": TEMPLATES["demo-skill"]})
    assert retired.read_text(encoding="utf-8") == TEMPLATES["other-skill"]


def test_seed_drops_tombstone_when_template_is_retired(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    (tmp_path / "other-skill" / "SKILL.md").unlink()

    seed_default_skills(tmp_path, {"demo-skill": TEMPLATES["demo-skill"]})

    assert "other-skill" not in _state(tmp_path)["skills"]


def test_seed_survives_corrupt_state_file(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    target = tmp_path / "demo-skill" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    (tmp_path / SEED_STATE_FILENAME).write_text("{not json", encoding="utf-8")

    seed_default_skills(tmp_path, TEMPLATES)

    assert target.read_text(encoding="utf-8") == original
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(original)


def test_seed_keeps_state_when_skill_is_unreadable(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    target = tmp_path / "demo-skill" / "SKILL.md"
    unreadable = b"\xff\xfe\x00"
    target.write_bytes(unreadable)

    seed_default_skills(tmp_path, {**TEMPLATES, "demo-skill": "changed\n"})

    assert target.read_bytes() == unreadable
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])


def test_seed_ignores_symlinked_skill_entry(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    real = tmp_path / "real-demo"
    (tmp_path / "demo-skill").rename(real)
    (tmp_path / "demo-skill").symlink_to(real, target_is_directory=True)

    seed_default_skills(tmp_path, {**TEMPLATES, "demo-skill": "changed\n"})

    assert (real / "SKILL.md").read_text(encoding="utf-8") == TEMPLATES["demo-skill"]
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])


def test_seed_drops_invalid_state_entries(tmp_path: Path) -> None:
    (tmp_path / SEED_STATE_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {"../escape": "deadbeef", "Bad Name": "deadbeef"},
            }
        ),
        encoding="utf-8",
    )

    seed_default_skills(tmp_path, TEMPLATES)

    state = _state(tmp_path)["skills"]
    assert "../escape" not in state
    assert "Bad Name" not in state


def test_load_seed_skills_reads_skill_md_files(tmp_path: Path) -> None:
    (tmp_path / "demo-skill").mkdir()
    (tmp_path / "demo-skill" / "SKILL.md").write_text(
        TEMPLATES["demo-skill"], encoding="utf-8"
    )
    (tmp_path / "no-skill-md").mkdir()
    (tmp_path / "note.txt").write_text("ignore me", encoding="utf-8")

    assert load_seed_skills(tmp_path) == {"demo-skill": TEMPLATES["demo-skill"]}


def test_load_seed_skills_returns_none_when_directory_missing(tmp_path: Path) -> None:
    assert load_seed_skills(tmp_path / "missing") is None


def test_load_seed_skills_returns_none_on_unreadable_template(tmp_path: Path) -> None:
    (tmp_path / "bad-skill").mkdir()
    (tmp_path / "bad-skill" / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    assert load_seed_skills(tmp_path) is None


def test_seed_leaves_state_untouched_when_templates_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    (tmp_path / "demo-skill" / "SKILL.md").unlink()
    before = _state(tmp_path)
    monkeypatch.setattr("app.services.skills.load_seed_skills", lambda root=None: None)

    seed_default_skills(tmp_path)

    assert _state(tmp_path) == before
    assert not (tmp_path / "demo-skill" / "SKILL.md").exists()


def test_seed_ignores_symlinked_skill_md(tmp_path: Path) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    target = tmp_path / "demo-skill" / "SKILL.md"
    external = tmp_path / "external.md"
    external.write_text("external content", encoding="utf-8")
    target.unlink()
    target.symlink_to(external)

    seed_default_skills(tmp_path, {**TEMPLATES, "demo-skill": "changed\n"})

    assert external.read_text(encoding="utf-8") == "external content"
    assert _state(tmp_path)["skills"]["demo-skill"] == _hash(TEMPLATES["demo-skill"])


def test_seed_writes_state_only_when_changed(tmp_path: Path, monkeypatch) -> None:
    seed_default_skills(tmp_path, TEMPLATES)
    calls: list[int] = []
    monkeypatch.setattr(
        "app.services.skills._write_seed_state", lambda base, state: calls.append(1)
    )

    seed_default_skills(tmp_path, TEMPLATES)

    assert calls == []


def test_real_seed_templates_cover_platform_domains() -> None:
    templates = load_seed_skills()
    assert set(templates) == {
        "quant-backtest",
        "cron-scheduler",
        "trade-journal",
        "allocation",
        "chanlun",
    }
    for name, content in templates.items():
        assert content.startswith("---\n")
        assert f"name: {name}" in content
