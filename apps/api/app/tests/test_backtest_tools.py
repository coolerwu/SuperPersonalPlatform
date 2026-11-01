from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.models import Backtest
from app.services import backtest_service, backtest_tools
from app.services.backtest_worker import set_active_worker
from app.services.market_data import MarketStatus
from app.services.skill_tools import SkillToolContext, SkillToolRegistry
from app.services.skills import scan_skills, seed_default_skills
from app.services.timeframes import HOUR_MS

START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = START + 100 * HOUR_MS
SKILL_TOOLS = {
    "backtest_run",
    "backtest_status",
    "backtest_result",
    "backtest_cancel",
    "market_probe",
    "market_cache_status",
}


@pytest.fixture(autouse=True)
def _reset_worker():
    set_active_worker(None)
    yield
    set_active_worker(None)


def _context() -> SkillToolContext:
    return SkillToolContext(api_key="", base_url="")


def _registry() -> SkillToolRegistry:
    registry = SkillToolRegistry()
    backtest_tools.register_backtest_tools(registry)
    return registry


def _tool(name: str):
    return _registry().resolve_names([name], _context())[0]


def _row(status: str = "completed", **overrides) -> Backtest:
    values = {
        "id": "bt-test",
        "symbol": "BTCUSDT",
        "granularity": "1h",
        "start_ms": START,
        "end_ms": END,
        "initial_capital": 10_000.0,
        "fee_rate": 0.001,
        "slippage_rate": 0.0005,
        "strategy_code": "def strategy(df):\n    return df['close'] * 0 + 1\n",
        "status": status,
        "error": None,
        "error_detail": None,
        "progress": {"stage": status},
        "metrics": {
            "bars": 100,
            "start_ms": START,
            "end_ms": END - HOUR_MS,
            "total_return": 0.1,
            "annual_return": 0.2,
            "max_drawdown": -0.05,
            "sharpe": 1.2,
            "sortino": 0.9,
            "win_rate": 0.5,
            "trades": 3,
            "profit_factor": 1.5,
            "benchmark_return": 0.05,
            "avg_exposure": 0.6,
        }
        if status == "completed"
        else None,
        "warnings": ["样本提示"],
        "data_start_ms": START,
        "data_end_ms": END - HOUR_MS,
        "bar_count": 100,
        "trades_total": 3,
        "trades_truncated": False,
        "trades": [
            {
                "entry_ts": START + index * HOUR_MS,
                "exit_ts": START + (index + 1) * HOUR_MS,
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "bars": 1,
                "return": 0.01,
                "pnl": 100.0,
                "closed": True,
            }
            for index in range(3)
        ],
    }
    values.update(overrides)
    return Backtest(**values)


def test_registers_backtest_tools() -> None:
    names = {spec.name for spec in _registry().specs()}
    assert SKILL_TOOLS <= names


async def test_backtest_run_formats_completed_result(monkeypatch) -> None:
    captured: dict = {}
    row = _row()
    payloads = []

    async def fake_create(payload):
        payloads.append(payload)
        return row

    async def fake_get(identifier):
        captured["id"] = identifier
        return row

    monkeypatch.setattr(backtest_service, "create_backtest", fake_create)
    monkeypatch.setattr(backtest_service, "get_backtest", fake_get)
    monkeypatch.setattr(settings, "backtest_tool_wait_seconds", 0)

    result = await _tool("backtest_run").ainvoke(
        {
            "symbol": "btcusdt",
            "granularity": "1h",
            "start": "2025-01-01",
            "end": "2025-01-05",
            "strategy_code": "def strategy(df):\n    return 1\n",
            "fee_pct": 0.1,
            "slippage_pct": 0.05,
        }
    )
    assert isinstance(result, str)
    assert "BTCUSDT" in result and "已完成" in result
    assert "总收益：10.00%" in result
    assert "告警：样本提示" in result
    assert captured["id"] == row.id
    payload = payloads[0]
    assert payload.symbol == "BTCUSDT"
    assert payload.fee_rate == pytest.approx(0.001)
    assert payload.slippage_rate == pytest.approx(0.0005)
    assert payload.start_ms == START


async def test_backtest_run_returns_id_when_still_running(monkeypatch) -> None:
    queued = _row("queued", metrics=None)
    running = _row(
        "running",
        metrics=None,
        progress={"stage": "fetching", "fetched_bars": 500},
    )
    calls = {"count": 0}

    async def fake_create(payload):
        return queued

    async def fake_get(identifier):
        calls["count"] += 1
        return running

    monkeypatch.setattr(backtest_service, "create_backtest", fake_create)
    monkeypatch.setattr(backtest_service, "get_backtest", fake_get)
    monkeypatch.setattr(settings, "backtest_tool_wait_seconds", 0)

    result = await _tool("backtest_run").ainvoke(
        {
            "symbol": "BTCUSDT",
            "granularity": "1h",
            "start": "2025-01-01",
            "end": "2025-01-05",
            "strategy_code": "def strategy(df):\n    return 1\n",
        }
    )
    assert "bt-test" in result
    assert "已抓取行情 500 根" in result
    assert "backtest_status" in result
    assert calls["count"] >= 1


async def test_backtest_run_rejects_invalid_granularity_and_dates() -> None:
    tool = _tool("backtest_run")
    bad_timeframe = await tool.ainvoke(
        {
            "symbol": "BTCUSDT",
            "granularity": "1m",
            "start": "2025-01-01",
            "end": "2025-01-05",
            "strategy_code": "def strategy(df):\n    return 1\n",
        }
    )
    assert bad_timeframe.startswith("Error:")
    bad_date = await tool.ainvoke(
        {
            "symbol": "BTCUSDT",
            "granularity": "1h",
            "start": "2025/01/01",
            "end": "2025-01-05",
            "strategy_code": "def strategy(df):\n    return 1\n",
        }
    )
    assert "YYYY-MM-DD" in bad_date


async def test_backtest_result_limits_trades(monkeypatch) -> None:
    row = _row()

    async def fake_get(identifier):
        return row

    monkeypatch.setattr(backtest_service, "get_backtest", fake_get)
    result = await _tool("backtest_result").ainvoke(
        {"backtest_id": "bt-test", "trade_limit": 2}
    )
    assert isinstance(result, str)
    assert "展示最近 2 笔" in result
    assert result.count("\n- ") == 2


async def test_backtest_result_reports_unfinished(monkeypatch) -> None:
    row = _row("running", metrics=None)

    async def fake_get(identifier):
        return row

    monkeypatch.setattr(backtest_service, "get_backtest", fake_get)
    result = await _tool("backtest_result").ainvoke({"backtest_id": "bt-test"})
    assert "回测尚未完成" in result


async def test_backtest_cancel_uses_service_without_worker(monkeypatch) -> None:
    async def fake_cancel(identifier):
        return True

    monkeypatch.setattr(backtest_service, "cancel_backtest", fake_cancel)
    result = await _tool("backtest_cancel").ainvoke({"backtest_id": "bt-test"})
    assert "已请求取消" in result


async def test_market_tools_format_probe_and_cache(monkeypatch) -> None:
    async def fake_probe(symbol, granularity):
        return True, 123, None

    async def fake_status(symbol, granularity):
        return MarketStatus(
            symbol="BTCUSDT",
            granularity="1h",
            rows=100,
            first_ms=START,
            last_ms=END - HOUR_MS,
            oldest_available_ms=None,
            last_synced_at=None,
            last_error=None,
        )

    from app.services import market_data

    monkeypatch.setattr(market_data.market_service, "probe", fake_probe)
    monkeypatch.setattr(market_data.market_service, "status", fake_status)

    probe = await _tool("market_probe").ainvoke(
        {"symbol": "BTCUSDT", "granularity": "1h"}
    )
    cache = await _tool("market_cache_status").ainvoke(
        {"symbol": "BTCUSDT", "granularity": "1h"}
    )
    assert "连接正常" in probe and "123 ms" in probe
    assert "已缓存 100 根" in cache


def test_quant_backtest_skill_is_seeded(tmp_path) -> None:
    seed_default_skills(tmp_path)
    catalog = scan_skills(tmp_path)
    skill = next(item for item in catalog.skills if item.name == "quant-backtest")
    assert set(skill.include_tools) == SKILL_TOOLS
    assert skill.source == "builtin"
