from __future__ import annotations

import re

import pytest

from app.config import settings
from app.services import allocation_tools  # noqa: F401 - registers the tools
from app.services.skill_tools import (
    SKILL_TOOL_REGISTRY,
    SkillToolContext,
    SkillToolRegistry,
)
from app.services.skills import DEFAULT_SKILLS, scan_skills, seed_default_skills


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _tools(agent_id: str = "default") -> dict:
    context = SkillToolContext(api_key="", base_url="", agent_id=agent_id)
    return {
        tool.name: tool
        for tool in SKILL_TOOL_REGISTRY.resolve_names(
            allocation_tools.TOOL_NAMES, context
        )
    }


def _portfolio_id(output: str) -> str:
    match = re.search(r"pf_[A-Za-z0-9]+", output)
    assert match, output
    return match.group(0)


def _entry_id(output: str) -> str:
    match = re.search(r"id=(al_[A-Za-z0-9]+)", output)
    assert match, output
    return match.group(1)


def test_allocation_skill_declares_tools(tmp_path) -> None:
    skills_root = tmp_path / "skills"
    seed_default_skills(skills_root)
    skills = {skill.name: skill for skill in scan_skills(skills_root).skills}

    assert "allocation" in DEFAULT_SKILLS
    assert "allocation" in skills
    assert set(skills["allocation"].include_tools) == set(
        allocation_tools.TOOL_NAMES
    )


def test_tools_are_registered_globally() -> None:
    for name in allocation_tools.TOOL_NAMES:
        assert SKILL_TOOL_REGISTRY.has(name)


async def test_create_record_overview_entries_and_update() -> None:
    tools = _tools()
    created = await tools["allocation_portfolio_create"].ainvoke(
        {
            "name": "长期核心",
            "currency": "USDT",
            "rebalance_band_pct": 5.0,
            "targets": {"BTCUSDT": 100.0},
            "marks": {"BTCUSDT": 65000.0},
            "note": "长期持有，只在偏离超过 5pp 时再平衡。",
        }
    )
    portfolio_id = _portfolio_id(created)

    empty = await tools["allocation_overview"].ainvoke(
        {"portfolio_id": portfolio_id}
    )
    assert "长期核心" in empty
    assert "BTCUSDT" in empty
    assert "计划中" in empty

    deposit = await tools["allocation_entry_record"].ainvoke(
        {
            "portfolio_id": portfolio_id,
            "type": "deposit",
            "at": "2025-01-01T09:00:00Z",
            "amount": 10000.0,
            "note": "初始入金",
        }
    )
    assert "已记录流水" in deposit

    buy = await tools["allocation_entry_record"].ainvoke(
        {
            "portfolio_id": portfolio_id,
            "type": "buy",
            "at": "2025-01-02T10:00:00Z",
            "symbol": "BTCUSDT",
            "quantity": 0.1,
            "price": 60000.0,
            "fees": 5.0,
            "note": "首次建仓",
        }
    )
    buy_id = _entry_id(buy)

    overview = await tools["allocation_overview"].ainvoke(
        {"portfolio_id": portfolio_id}
    )
    assert "BTCUSDT" in overview
    assert "10,495.00 USDT" in overview
    assert "目标 100.00%" in overview
    assert "再平衡" in overview

    entries = await tools["allocation_entries"].ainvoke(
        {"portfolio_id": portfolio_id, "limit": 20}
    )
    assert "共 2 笔" in entries
    assert "入金" in entries
    assert "买入" in entries

    updated = await tools["allocation_portfolio_update"].ainvoke(
        {
            "portfolio_id": portfolio_id,
            "targets": {"BTCUSDT": 50.0, "ETHUSDT": 50.0},
            "note": "调整目标",
        }
    )
    assert "已更新组合" in updated
    overview = await tools["allocation_overview"].ainvoke(
        {"portfolio_id": portfolio_id}
    )
    assert "ETHUSDT 50.00%" in overview
    assert "目标合计 100.00%" in overview

    corrected = await tools["allocation_entry_update"].ainvoke(
        {
            "portfolio_id": portfolio_id,
            "entry_id": buy_id,
            "note": "补记：首次建仓",
        }
    )
    assert "已更新流水" in corrected
    entries = await tools["allocation_entries"].ainvoke(
        {"portfolio_id": portfolio_id, "limit": 20}
    )
    assert "补记：首次建仓" in entries

    oversell = await tools["allocation_entry_record"].ainvoke(
        {
            "portfolio_id": portfolio_id,
            "type": "sell",
            "at": "2025-01-03T10:00:00Z",
            "symbol": "BTCUSDT",
            "quantity": 1.0,
            "price": 65000.0,
        }
    )
    assert oversell.startswith("Error:")
    assert "超过当时持仓" in oversell


async def test_overview_without_portfolios_and_unknown_id() -> None:
    tools = _tools()
    empty = await tools["allocation_overview"].ainvoke({})
    assert "还没有资产配置组合" in empty

    missing = await tools["allocation_overview"].ainvoke(
        {"portfolio_id": "pf_missing00"}
    )
    assert missing.startswith("Error:")

    entries = await tools["allocation_entries"].ainvoke(
        {"portfolio_id": "pf_missing00"}
    )
    assert entries.startswith("Error:")
