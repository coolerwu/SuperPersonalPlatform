from __future__ import annotations

import json

import pytest

from app.services import platform_mcp
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext


def _context() -> SkillToolContext:
    return SkillToolContext(
        api_key="", base_url="", agent_id="default", session_id="s"
    )


def test_business_tool_names_are_registered() -> None:
    registered = {spec.name for spec in SKILL_TOOL_REGISTRY.specs()}
    missing = set(platform_mcp.BUSINESS_TOOL_NAMES) - registered
    assert missing == set()
    assert len(set(platform_mcp.BUSINESS_TOOL_NAMES)) == len(
        platform_mcp.BUSINESS_TOOL_NAMES
    )
    assert "browser_open" not in platform_mcp.BUSINESS_TOOL_NAMES
    assert "web_search" not in platform_mcp.BUSINESS_TOOL_NAMES


def test_allowed_tool_names_are_prefixed() -> None:
    names = platform_mcp.allowed_tool_names()
    assert len(names) == len(platform_mcp.BUSINESS_TOOL_NAMES)
    assert "mcp__platform_tools__backtest_run" in names
    assert all(name.startswith("mcp__platform_tools__") for name in names)


def test_adapt_tool_uses_json_schema() -> None:
    spec = next(
        spec
        for spec in SKILL_TOOL_REGISTRY.specs()
        if spec.name == "backtest_run"
    )
    adapted = platform_mcp._adapt_tool(spec.factory(_context()))
    assert adapted.name == "backtest_run"
    assert adapted.input_schema["type"] == "object"
    assert "strategy_code" in adapted.input_schema["properties"]


@pytest.mark.asyncio
async def test_adapt_tool_returns_text() -> None:
    spec = next(
        spec
        for spec in SKILL_TOOL_REGISTRY.specs()
        if spec.name == "trade_journal_stats"
    )
    adapted = platform_mcp._adapt_tool(spec.factory(_context()))
    result = await adapted.handler({})
    assert result.get("is_error") is not True
    assert "交易统计" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_adapt_tool_serializes_dict_result() -> None:
    class DictTool:
        name = "dict_tool"
        description = "dict"
        args_schema = None

        async def ainvoke(self, args):
            return {"ok": True, "count": 2}

    adapted = platform_mcp._adapt_tool(DictTool())
    result = await adapted.handler({})
    assert json.loads(result["content"][0]["text"]) == {
        "ok": True,
        "count": 2,
    }


@pytest.mark.asyncio
async def test_adapt_tool_reports_exceptions() -> None:
    class FailingTool:
        name = "failing_tool"
        description = "fails"
        args_schema = None

        async def ainvoke(self, args):
            raise RuntimeError("boom")

    adapted = platform_mcp._adapt_tool(FailingTool())
    result = await adapted.handler({})
    assert result["is_error"] is True
    assert "boom" in result["content"][0]["text"]


def test_all_business_tool_schemas_are_objects() -> None:
    specs = {spec.name: spec for spec in SKILL_TOOL_REGISTRY.specs()}
    for name in platform_mcp.BUSINESS_TOOL_NAMES:
        adapted = platform_mcp._adapt_tool(specs[name].factory(_context()))
        assert adapted.input_schema.get("type") == "object", name
        assert isinstance(adapted.input_schema.get("properties"), dict), name
        json.dumps(adapted.input_schema)


def test_tool_timeout_grows_with_backtest_wait(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_mcp.app_settings, "backtest_tool_wait_seconds", 300
    )
    assert platform_mcp._tool_timeout_ms() == 360_000


def test_build_server_returns_sdk_config() -> None:
    config = platform_mcp.build_server(_context())
    assert config["type"] == "sdk"
    assert config["name"] == "platform_tools"
    assert config["instance"] is not None
    assert config["timeout"] >= 180_000
    assert config["disableAutoBackground"] is True
