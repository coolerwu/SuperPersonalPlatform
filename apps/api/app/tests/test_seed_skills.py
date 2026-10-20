from __future__ import annotations

import hashlib
import json

from app.services.skills import DEFAULT_SKILLS, SEED_STATE_FILENAME, scan_skills, seed_default_skills


def _state(root) -> dict:
    return json.loads((root / SEED_STATE_FILENAME).read_text(encoding="utf-8"))


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_seed_creates_missing_skills_and_state(tmp_path) -> None:
    seed_default_skills(tmp_path)

    for name, content in DEFAULT_SKILLS.items():
        assert (tmp_path / name / "SKILL.md").read_text(encoding="utf-8") == content
    state = _state(tmp_path)
    assert state["version"] == 1
    assert state["skills"] == {name: _hash(content) for name, content in DEFAULT_SKILLS.items()}
    assert {skill.name for skill in scan_skills(tmp_path).skills} == set(DEFAULT_SKILLS)


def test_seed_updates_unmodified_builtin_skill(tmp_path, monkeypatch) -> None:
    seed_default_skills(tmp_path)
    name = "web-browser"
    updated = DEFAULT_SKILLS[name] + "\nupdated built-in content\n"
    monkeypatch.setitem(DEFAULT_SKILLS, name, updated)

    seed_default_skills(tmp_path)

    assert (tmp_path / name / "SKILL.md").read_text(encoding="utf-8") == updated
    assert _state(tmp_path)["skills"][name] == _hash(updated)


def test_seed_preserves_user_edited_skill(tmp_path, monkeypatch) -> None:
    seed_default_skills(tmp_path)
    target = tmp_path / "web-browser" / "SKILL.md"
    target.write_text("user customized content", encoding="utf-8")
    monkeypatch.setitem(DEFAULT_SKILLS, "web-browser", "new built-in content")

    seed_default_skills(tmp_path)

    assert target.read_text(encoding="utf-8") == "user customized content"


def test_seed_adopts_existing_file_that_matches_default(tmp_path, monkeypatch) -> None:
    name = "web-browser"
    target = tmp_path / name / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(DEFAULT_SKILLS[name], encoding="utf-8")
    assert not (tmp_path / SEED_STATE_FILENAME).exists()

    seed_default_skills(tmp_path)
    assert _state(tmp_path)["skills"][name] == _hash(DEFAULT_SKILLS[name])

    updated = DEFAULT_SKILLS[name] + "\nnext\n"
    monkeypatch.setitem(DEFAULT_SKILLS, name, updated)
    seed_default_skills(tmp_path)
    assert target.read_text(encoding="utf-8") == updated


def test_seed_survives_corrupt_state_file(tmp_path) -> None:
    seed_default_skills(tmp_path)
    target = tmp_path / "web-browser" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    (tmp_path / SEED_STATE_FILENAME).write_text("{not json", encoding="utf-8")

    seed_default_skills(tmp_path)

    assert target.read_text(encoding="utf-8") == original
    assert _state(tmp_path)["skills"]["web-browser"] == _hash(original)
