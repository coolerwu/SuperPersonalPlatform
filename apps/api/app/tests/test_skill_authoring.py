from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal, init_db
from app.main import create_app
from app.models import Agent, Provider
from app.services.skill_tools import SkillToolContext, build_skill_save_tool
from app.services.skills import (
    SkillValidationError,
    create_skill,
    delete_skill,
    scan_skills,
    seed_default_skills,
)


def test_create_skill_marks_source_and_tools(tmp_path) -> None:
    document = create_skill(
        "daily-review",
        "每天复盘时使用。",
        "1. 读取记录\n2. 输出结论",
        include_tools="read_file grep",
        root=tmp_path,
    )

    assert document.source == "ai"
    text = (tmp_path / "daily-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "source: ai" in text
    catalog = scan_skills(tmp_path)
    skill = catalog.skills[0]
    assert skill.source == "ai"
    assert skill.include_tools == ("read_file", "grep")


def test_create_skill_refuses_existing_and_builtin(tmp_path) -> None:
    create_skill("dup-skill", "描述", "正文", root=tmp_path)
    with pytest.raises(SkillValidationError):
        create_skill("dup-skill", "描述", "正文", root=tmp_path)

    seed_default_skills(tmp_path)
    with pytest.raises(SkillValidationError):
        create_skill("web-search", "描述", "正文", root=tmp_path)


@pytest.mark.parametrize("name", ["Bad", "-bad", "bad name", "a--b", ""])
def test_create_skill_validates_name(tmp_path, name: str) -> None:
    with pytest.raises(SkillValidationError):
        create_skill(name, "描述", "正文", root=tmp_path)


def test_delete_skill_refuses_builtin_and_removes_created(tmp_path) -> None:
    seed_default_skills(tmp_path)
    with pytest.raises(SkillValidationError):
        delete_skill("web-search", root=tmp_path)
    with pytest.raises(SkillValidationError):
        delete_skill(" web-search ", root=tmp_path)
    assert (tmp_path / "web-search" / "SKILL.md").is_file()

    create_skill("temp-skill", "描述", "正文", root=tmp_path)
    delete_skill("temp-skill", root=tmp_path)
    assert not (tmp_path / "temp-skill").exists()


@pytest.mark.asyncio
async def test_skill_save_tool_creates_ai_skill(tmp_path) -> None:
    context = SkillToolContext(api_key="", base_url="", skills_dir=tmp_path)
    tool = build_skill_save_tool(context)

    result = await tool.ainvoke(
        {
            "name": "weekly-report",
            "description": "每周汇总时使用。",
            "instructions": "1. 收集数据\n2. 输出报告",
            "include_tools": "read_file",
        }
    )
    assert "已创建技能 weekly-report" in result
    assert "未能自动启用" in result
    text = (tmp_path / "weekly-report" / "SKILL.md").read_text(encoding="utf-8")
    assert "source: ai" in text
    assert "include_tools: read_file" in text

    again = await tool.ainvoke(
        {
            "name": "weekly-report",
            "description": "每周汇总时使用。",
            "instructions": "重复创建",
        }
    )
    assert again.startswith("Error: skill weekly-report already exists")


@pytest.mark.asyncio
async def test_skill_save_tool_enables_created_skill_for_agent(tmp_path) -> None:
    await init_db()
    async with SessionLocal() as db:
        if await db.get(Provider, "default") is None:
            db.add(Provider(id="default", name="default", base_url="", api_key="", model=""))
            await db.commit()

    agent_id = f"skill-agent-{uuid.uuid4().hex[:12]}"
    async with SessionLocal() as db:
        db.add(
            Agent(
                id=agent_id,
                name=agent_id,
                instructions="",
                provider_id="default",
                enabled_skills=[],
            )
        )
        await db.commit()

    context = SkillToolContext(
        api_key="", base_url="", agent_id=agent_id, skills_dir=tmp_path
    )
    tool = build_skill_save_tool(context)
    result = await tool.ainvoke(
        {
            "name": "auto-enabled-skill",
            "description": "自动启用验证。",
            "instructions": "步骤。",
        }
    )
    assert "下一次对话生效" in result

    async with SessionLocal() as db:
        agent = await db.get(Agent, agent_id)
        assert agent is not None
        assert "auto-enabled-skill" in agent.enabled_skills


@pytest.mark.asyncio
async def test_skill_save_tool_limits_creations_per_run(tmp_path) -> None:
    context = SkillToolContext(api_key="", base_url="", skills_dir=tmp_path)
    tool = build_skill_save_tool(context)

    for index in range(3):
        result = await tool.ainvoke(
            {
                "name": f"skill-{index}",
                "description": "可复用流程。",
                "instructions": "步骤。",
            }
        )
        assert "已创建技能" in result

    blocked = await tool.ainvoke(
        {
            "name": "skill-over-limit",
            "description": "可复用流程。",
            "instructions": "步骤。",
        }
    )
    assert blocked.startswith("Error:")
    assert "最多创建" in blocked
    assert not (tmp_path / "skill-over-limit").exists()


def test_skills_api_reports_sources(client) -> None:
    create_skill("api-ai-skill", "API 创建技能", "正文", root=settings.skills_dir)

    body = client.get("/api/skills").json()
    skills = {item["name"]: item for item in body["skills"]}
    assert skills["web-search"]["source"] == "builtin"
    assert skills["api-ai-skill"]["source"] == "ai"

    detail = client.get("/api/skills/api-ai-skill").json()
    assert detail["source"] == "ai"


def test_delete_skill_api_removes_created_and_enabled_reference(client) -> None:
    create_skill("api-delete-me", "待删除技能", "正文", root=settings.skills_dir)
    providers = client.get("/api/providers").json()
    assert providers
    agent = client.post(
        "/api/agents",
        json={
            "name": "Skill Agent",
            "instructions": "",
            "provider_id": providers[0]["id"],
        },
    ).json()
    updated = client.put(
        f"/api/agents/{agent['id']}",
        json={"enabled_skills": ["api-delete-me"]},
    )
    assert updated.status_code == 200
    assert "api-delete-me" in updated.json()["enabled_skills"]

    response = client.delete("/api/skills/api-delete-me")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not (settings.skills_dir / "api-delete-me").exists()

    reloaded = client.get(f"/api/agents/{agent['id']}").json()
    assert "api-delete-me" not in reloaded["enabled_skills"]
    assert all(item["name"] != "api-delete-me" for item in client.get("/api/skills").json()["skills"])


def test_delete_skill_api_refuses_builtin_and_unknown(client) -> None:
    assert client.delete("/api/skills/web-search").status_code == 409
    assert (settings.skills_dir / "web-search" / "SKILL.md").is_file()
    assert client.delete("/api/skills/not-installed").status_code == 404


def test_delete_skill_api_strips_declared_name_when_directory_differs(client) -> None:
    folder = settings.skills_dir / "dir-name-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: declared-name-skill\ndescription: 名称与目录不一致\n---\n\n正文\n",
        encoding="utf-8",
    )
    providers = client.get("/api/providers").json()
    agent = client.post(
        "/api/agents",
        json={
            "name": "Mismatch Agent",
            "instructions": "",
            "provider_id": providers[0]["id"],
        },
    ).json()
    updated = client.put(
        f"/api/agents/{agent['id']}",
        json={"enabled_skills": ["declared-name-skill"]},
    )
    assert updated.status_code == 200

    assert client.delete("/api/skills/dir-name-skill").status_code == 200
    reloaded = client.get(f"/api/agents/{agent['id']}").json()
    assert "declared-name-skill" not in reloaded["enabled_skills"]


def test_delete_skill_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.delete("/api/skills/web-search").status_code == 401
