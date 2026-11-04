from __future__ import annotations

import re

import pytest

from app.config import settings
from app.services import trade_journal_tools  # noqa: F401 - registers the tools
from app.services.skill_tools import (
    SKILL_TOOL_REGISTRY,
    SkillToolContext,
    SkillToolRegistry,
)
from app.services.skills import DEFAULT_SKILLS, scan_skills, seed_default_skills


@pytest.fixture(autouse=True)
def _journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _tools(agent_id: str = "default") -> dict:
    context = SkillToolContext(api_key="", base_url="", agent_id=agent_id)
    return {
        tool.name: tool
        for tool in SKILL_TOOL_REGISTRY.resolve_names(
            trade_journal_tools.TOOL_NAMES, context
        )
    }


def _record_payload(**overrides) -> dict:
    payload = {
        "symbol": "600519.SH",
        "market": "a_share",
        "direction": "long",
        "entry_at": "2026-11-02T10:15:00+08:00",
        "entry_price": 1450.0,
        "stop_loss": 1418.0,
        "quantity": 100,
        "thesis": "日线回踩 20 日线缩量企稳",
        "tags": ["setup:趋势回踩", "board:主板"],
    }
    payload.update(overrides)
    return payload


def _extract_id(output: str) -> str:
    match = re.search(r"id:\s*(tj_[A-Za-z0-9]+)", output)
    assert match, output
    return match.group(1)


def test_trade_journal_skill_declares_tools(tmp_path) -> None:
    skills_root = tmp_path / "skills"
    seed_default_skills(skills_root)
    skills = {skill.name: skill for skill in scan_skills(skills_root).skills}

    assert "trade-journal" in DEFAULT_SKILLS
    assert "trade-journal" in skills
    assert set(skills["trade-journal"].include_tools) == set(
        trade_journal_tools.TOOL_NAMES
    )


def test_tools_are_registered_globally() -> None:
    for name in trade_journal_tools.TOOL_NAMES:
        assert SKILL_TOOL_REGISTRY.has(name)


async def test_record_query_close_and_stats() -> None:
    tools = _tools()
    created = await tools["trade_journal_record"].ainvoke(_record_payload())
    assert "已写入" in created
    record_id = _extract_id(created)

    detail = await tools["trade_journal_query"].ainvoke({"record_id": record_id})
    assert "600519.SH" in detail
    assert "日线回踩" in detail
    assert "agent_id" in detail

    closed = await tools["trade_journal_close"].ainvoke(
        {
            "record_id": record_id,
            "exit_at": "2026-11-03T14:30:00+08:00",
            "exit_price": 1498.0,
            "fees": 31.2,
            "review": "按计划持有，冲高减半",
            "followed_plan": True,
        }
    )
    assert "已平仓" in closed
    assert "按计划持有" in closed

    stats = await tools["trade_journal_stats"].ainvoke({"market": "a_share"})
    assert "胜率" in stats
    assert "100.00%" in stats
    assert "按标签" in stats


async def test_update_tool_changes_only_given_fields() -> None:
    tools = _tools()
    created = await tools["trade_journal_record"].ainvoke(_record_payload())
    record_id = _extract_id(created)

    updated = await tools["trade_journal_update"].ainvoke(
        {"record_id": record_id, "review": "补充复盘", "followed_plan": False}
    )
    assert "已更新" in updated
    assert "补充复盘" in updated
    assert "是否按计划: 否" in updated

    empty = await tools["trade_journal_update"].ainvoke({"record_id": record_id})
    assert empty.startswith("Error:")


async def test_tool_validation_returns_error_text() -> None:
    tools = _tools()
    bad = await tools["trade_journal_record"].ainvoke(
        _record_payload(direction="flat")
    )
    assert bad.startswith("Error:")

    bad_date = await tools["trade_journal_query"].ainvoke({"start": "not-a-date"})
    assert bad_date.startswith("Error:")


async def test_tools_are_scoped_to_the_calling_agent_meta() -> None:
    tools = _tools("agent-abc")
    created = await tools["trade_journal_record"].ainvoke(_record_payload())
    record_id = _extract_id(created)
    detail = await tools["trade_journal_query"].ainvoke({"record_id": record_id})
    assert '"agent_id":"agent-abc"' in detail


def test_registry_can_register_tools_in_isolation() -> None:
    registry = SkillToolRegistry()
    trade_journal_tools.register_trade_journal_tools(registry)
    assert {spec.name for spec in registry.specs()} == set(
        trade_journal_tools.TOOL_NAMES
    )
