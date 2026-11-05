from __future__ import annotations

import pytest

from app.services import chan_analysis, chan_tools  # noqa: F401 - registers the tool
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext
from app.services.skills import DEFAULT_SKILLS, scan_skills, seed_default_skills


def _tool():
    context = SkillToolContext(api_key="", base_url="")
    return SKILL_TOOL_REGISTRY.resolve_names(["chan_analyze"], context)[0]


def _result() -> dict:
    return {
        "symbol": "BTCUSDT",
        "granularity": "1h",
        "start_ms": 1_792_800_000_000,
        "end_ms": 1_793_880_000_000,
        "bars": 300,
        "merged_bars": 215,
        "stroke_count": 25,
        "zone_count": 2,
        "point_count": 3,
        "zones": [
            {"start_time": 1_792_900_000_000, "end_time": 1_793_100_000_000, "zg": 81000, "zd": 80000}
        ],
        "strokes": [
            {
                "direction": "down",
                "start_time": 1_792_800_000_000,
                "start_price": 82000,
                "end_time": 1_792_880_000_000,
                "end_price": 80000,
            }
        ],
        "points": [
            {
                "type": "1buy",
                "time": 1_792_880_000_000,
                "price": 80000,
                "reason": "价格创新低但 MACD 面积缩小",
            },
            {
                "type": "2buy",
                "time": 1_792_920_000_000,
                "price": 80500,
                "reason": "1 买后回踩不破",
            },
        ],
        "rules": {
            "name": "程序化缠论 · 老笔",
            "bi_rule": "顶底分型交替，至少 5 根合并 K 线",
            "point_rule": "笔级别近似",
        },
        "warnings": [],
    }


async def test_chan_analyze_tool_formats_summary(monkeypatch) -> None:
    async def fake_analyze(symbol: str, granularity: str, *, bars: int = 300) -> dict:
        assert symbol == "BTCUSDT"
        assert granularity == "1h"
        assert bars == 300
        return _result()

    monkeypatch.setattr(chan_analysis, "analyze_symbol", fake_analyze)
    output = await _tool().ainvoke({"symbol": "BTCUSDT"})
    assert "缠论结构 · BTCUSDT 1h" in output
    assert "中枢" in output
    assert "81,000" in output
    assert "1买" in output
    assert "2买" in output
    assert "不是交易建议" in output


async def test_chan_analyze_tool_returns_error_text(monkeypatch) -> None:
    async def fake_analyze(symbol: str, granularity: str, *, bars: int = 300) -> dict:
        raise chan_analysis.ChanError("只支持 1h、4h、1day")

    monkeypatch.setattr(chan_analysis, "analyze_symbol", fake_analyze)
    output = await _tool().ainvoke({"symbol": "BTCUSDT", "granularity": "5min"})
    assert output.startswith("Error:")
    assert "1h" in output


def test_chanlun_skill_declares_tool(tmp_path) -> None:
    seed_default_skills(tmp_path)
    skills = {skill.name: skill for skill in scan_skills(tmp_path).skills}
    assert "chanlun" in DEFAULT_SKILLS
    assert "chanlun" in skills
    assert set(skills["chanlun"].include_tools) == {"chan_analyze"}
    assert SKILL_TOOL_REGISTRY.has("chan_analyze")
