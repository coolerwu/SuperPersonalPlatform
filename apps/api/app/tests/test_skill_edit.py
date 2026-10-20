from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.skills import SkillNotFoundError, read_skill

VALID = """---
name: {name}
description: {description}
---

# {name}

正文。
"""


def _write_skill(name: str, content: str) -> None:
    directory = settings.skills_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(content, encoding="utf-8")


def test_get_skill_returns_content(client) -> None:
    content = VALID.format(name="demo-view", description="可查看技能")
    _write_skill("demo-view", content)

    response = client.get("/api/skills/demo-view")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "demo-view"
    assert body["directory"] == "demo-view"
    assert body["path"] == "/skills/demo-view/SKILL.md"
    assert body["content"] == content


def test_update_skill_writes_file_and_refreshes_catalog(client) -> None:
    _write_skill("demo-edit", VALID.format(name="demo-edit", description="旧描述"))
    updated = VALID.format(name="demo-edit", description="更新后的描述")

    response = client.put("/api/skills/demo-edit", json={"content": updated})
    assert response.status_code == 200
    assert response.json()["content"] == updated
    assert (settings.skills_dir / "demo-edit" / "SKILL.md").read_text(encoding="utf-8") == updated

    catalog = client.get("/api/skills").json()
    skill = next(item for item in catalog["skills"] if item["name"] == "demo-edit")
    assert skill["description"] == "更新后的描述"


def test_update_skill_rejects_invalid_frontmatter(client) -> None:
    original = VALID.format(name="demo-invalid", description="保持原样")
    _write_skill("demo-invalid", original)

    response = client.put("/api/skills/demo-invalid", json={"content": "# 没有 frontmatter"})
    assert response.status_code == 422
    assert (settings.skills_dir / "demo-invalid" / "SKILL.md").read_text(encoding="utf-8") == original


def test_update_skill_rejects_missing_name(client) -> None:
    _write_skill("demo-name", VALID.format(name="demo-name", description="描述"))
    response = client.put(
        "/api/skills/demo-name",
        json={"content": "---\ndescription: 没有名字\n---\n\n正文\n"},
    )
    assert response.status_code == 422


def test_get_and_update_unknown_skill(client) -> None:
    assert client.get("/api/skills/not-installed").status_code == 404
    assert (
        client.put("/api/skills/not-installed", json={"content": "whatever"}).status_code
        == 404
    )


def test_skill_paths_cannot_escape_root(client) -> None:
    assert client.get("/api/skills/.hidden").status_code == 404
    with pytest.raises(SkillNotFoundError):
        read_skill("..")


def test_skill_editor_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/skills/web-search").status_code == 401
        assert (
            anonymous.put("/api/skills/web-search", json={"content": "---\nname: x\ndescription: y\n---\n"}).status_code
            == 401
        )
