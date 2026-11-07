from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services import allocation, stock_quotes
from app.services.stock_quotes import FxQuote, StockQuote, StockQuoteError


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _at(day: int, hour: int = 0) -> str:
    return f"2025-01-{day:02d}T{hour:02d}:00:00Z"


async def _portfolio(**overrides):
    payload = {
        "name": "长期核心",
        "currency": "USDT",
        "cash_target_pct": None,
        "rebalance_band_pct": 5.0,
        "targets": {},
        "marks": {},
        "note": "",
    }
    payload.update(overrides)
    return await allocation.create_portfolio(payload)


async def _entry(portfolio_id: str, entry_type: str, at: str, **fields):
    return await allocation.create_entry(
        portfolio_id, {"type": entry_type, "at": at, **fields}
    )


async def _entries(portfolio_id: str):
    return (await allocation.list_entries(portfolio_id, limit=200))["items"]


async def _snapshot(portfolio_id: str, **kwargs):
    config = await allocation.load_config()
    portfolio = next(item for item in config["portfolios"] if item["id"] == portfolio_id)
    entries = await _entries(portfolio_id)
    return allocation.build_portfolio_snapshot(portfolio, entries, **kwargs)


async def test_portfolio_crud_and_config_file(tmp_path) -> None:
    portfolio = await _portfolio(name="核心", targets={"BTCUSDT": 60.0})
    assert portfolio["id"].startswith("pf_")
    assert portfolio["name"] == "核心"
    assert portfolio["targets"] == {"BTCUSDT": 60.0}
    assert portfolio["rebalance_band_pct"] == 5.0

    config = await allocation.load_config()
    assert len(config["portfolios"]) == 1
    path = settings.allocation_path
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600

    updated = await allocation.update_portfolio(
        portfolio["id"], {"name": "核心组合", "rebalance_band_pct": 8.0}
    )
    assert updated["name"] == "核心组合"
    assert updated["rebalance_band_pct"] == 8.0

    with pytest.raises(allocation.AllocationError):
        await allocation.update_portfolio(
            portfolio["id"], {"marks": {"BTCUSDT": {"price": None}}}
        )

    await _entry(portfolio["id"], "deposit", _at(1), amount=1000)
    with pytest.raises(allocation.AllocationError):
        await allocation.update_portfolio(portfolio["id"], {"currency": "CNY"})
    with pytest.raises(allocation.AllocationConflict):
        await allocation.delete_portfolio(portfolio["id"])

    empty = await _portfolio(name="空组合", currency="CNY")
    preserved = await allocation.update_portfolio(empty["id"], {"currency": None})
    assert preserved["currency"] == "CNY"
    await allocation.delete_portfolio(empty["id"])
    config = await allocation.load_config()
    assert [item["id"] for item in config["portfolios"]] == [portfolio["id"]]


async def test_deposit_buy_sell_and_costs() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=10000)
    await _entry(
        pid, "buy", _at(2), symbol="BTCUSDT", quantity=0.1, price=60000, fees=5
    )
    await _entry(
        pid, "buy", _at(3), symbol="BTCUSDT", quantity=0.1, price=70000, fees=5
    )
    await _entry(
        pid, "sell", _at(4), symbol="BTCUSDT", quantity=0.05, price=80000, fees=2
    )

    snapshot = await _snapshot(
        pid,
        cached_prices={
            "BTCUSDT": {"price": 75000.0, "at": _at(5), "source": "cache"}
        },
    )
    position = snapshot["positions"][0]
    assert snapshot["cash"] == pytest.approx(988.0)
    assert snapshot["net_contributions"] == pytest.approx(10000.0)
    assert snapshot["realized_pnl"] == pytest.approx(745.5)
    assert snapshot["unrealized_pnl"] == pytest.approx(1492.5)
    assert snapshot["total_value"] == pytest.approx(12238.0)
    assert snapshot["total_return"] == pytest.approx(2238.0)
    assert snapshot["total_return_pct"] == pytest.approx(22.38)
    assert position["quantity"] == pytest.approx(0.15)
    assert position["avg_cost"] == pytest.approx(65050.0)
    assert position["cost"] == pytest.approx(9757.5)
    assert position["market_value"] == pytest.approx(11250.0)
    assert position["weight_pct"] == pytest.approx(11250 / 12238 * 100)
    assert position["price_source"] == "cache"


async def test_opening_dividend_and_fee() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=1000)
    await _entry(
        pid, "opening", _at(2), symbol="AAA", quantity=10, price=100, fees=0
    )
    await _entry(pid, "dividend", _at(3), symbol="AAA", amount=20)
    await _entry(pid, "fee", _at(4), amount=5)

    snapshot = await _snapshot(
        pid,
        cached_prices={"AAA": {"price": 110.0, "at": _at(5), "source": "cache"}},
    )
    position = snapshot["positions"][0]
    assert snapshot["cash"] == pytest.approx(1015.0)
    assert snapshot["net_contributions"] == pytest.approx(2000.0)
    assert snapshot["dividends"] == pytest.approx(20.0)
    assert snapshot["fees"] == pytest.approx(5.0)
    assert snapshot["total_value"] == pytest.approx(2115.0)
    assert snapshot["total_return"] == pytest.approx(115.0)
    assert snapshot["unrealized_pnl"] == pytest.approx(100.0)
    assert position["dividends"] == pytest.approx(20.0)
    assert position["realized_pnl"] == pytest.approx(0.0)


async def test_oversell_is_rejected_on_create_update_and_delete() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=1000)
    buy = await _entry(
        pid, "buy", _at(2), symbol="BTCUSDT", quantity=1, price=100
    )
    with pytest.raises(allocation.AllocationError):
        await _entry(pid, "sell", _at(3), symbol="BTCUSDT", quantity=2, price=120)

    sell = await _entry(
        pid, "sell", _at(3), symbol="BTCUSDT", quantity=0.8, price=120
    )
    with pytest.raises(allocation.AllocationError):
        await allocation.update_entry(pid, buy["id"], {"quantity": 0.5})
    await allocation.update_entry(pid, sell["id"], {"quantity": 0.5})
    with pytest.raises(allocation.AllocationError):
        await allocation.delete_entry(pid, buy["id"])


async def test_backdated_entry_replays_cost_and_realized_pnl() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=10000)
    await _entry(pid, "buy", _at(2), symbol="BTCUSDT", quantity=1, price=100)
    await _entry(pid, "sell", _at(4), symbol="BTCUSDT", quantity=1, price=120)
    await _entry(pid, "buy", _at(3), symbol="BTCUSDT", quantity=0.5, price=80)

    snapshot = await _snapshot(
        pid,
        cached_prices={
            "BTCUSDT": {"price": 100.0, "at": _at(5), "source": "cache"}
        },
    )
    position = snapshot["positions"][0]
    assert position["quantity"] == pytest.approx(0.5)
    assert position["avg_cost"] == pytest.approx(140 / 1.5)
    assert position["realized_pnl"] == pytest.approx(120 - (140 / 1.5) * 1)
    assert snapshot["cash"] == pytest.approx(9980.0)
    assert snapshot["total_return"] == pytest.approx(30.0)


async def test_targets_drift_band_and_rebalance() -> None:
    portfolio = await _portfolio(
        targets={"BTCUSDT": 50.0, "ETHUSDT": 50.0},
        cash_target_pct=0.0,
        rebalance_band_pct=5.0,
    )
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=10000)
    await _entry(pid, "buy", _at(2), symbol="BTCUSDT", quantity=1, price=6000)

    snapshot = await _snapshot(
        pid,
        cached_prices={
            "BTCUSDT": {"price": 6000.0, "at": _at(3), "source": "cache"}
        },
    )
    positions = {item["symbol"]: item for item in snapshot["positions"]}
    btc = positions["BTCUSDT"]
    eth = positions["ETHUSDT"]
    assert snapshot["total_value"] == pytest.approx(10000.0)
    assert btc["weight_pct"] == pytest.approx(60.0)
    assert btc["drift_pp"] == pytest.approx(10.0)
    assert btc["rebalance_needed"] is True
    assert btc["rebalance_amount"] == pytest.approx(-1000.0)
    assert eth["target_pct"] == pytest.approx(50.0)
    assert eth["weight_pct"] == pytest.approx(0.0)
    assert eth["rebalance_amount"] == pytest.approx(5000.0)
    assert snapshot["cash_weight_pct"] == pytest.approx(40.0)
    assert snapshot["cash_drift_pp"] == pytest.approx(40.0)
    assert snapshot["cash_rebalance_needed"] is True
    assert snapshot["target_sum_pct"] == pytest.approx(100.0)
    assert snapshot["unallocated_pct"] == pytest.approx(0.0)


async def test_target_sum_warning_does_not_block_save() -> None:
    portfolio = await _portfolio(targets={"BTCUSDT": 60.0})
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=1000)
    snapshot = await _snapshot(pid)
    assert any("未分配" in warning for warning in snapshot["warnings"])

    await allocation.update_portfolio(
        pid, {"targets": {"BTCUSDT": 60.0, "ETHUSDT": 60.0}}
    )
    snapshot = await _snapshot(pid)
    assert any("超过 100%" in warning for warning in snapshot["warnings"])
    with pytest.raises(allocation.AllocationError):
        await allocation.update_portfolio(pid, {"targets": {"BTCUSDT": 120.0}})


async def test_price_precedence_mark_cache_cost() -> None:
    portfolio = await _portfolio(marks={"BTCUSDT": {"price": 70000.0}})
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=10000)
    await _entry(pid, "buy", _at(2), symbol="BTCUSDT", quantity=0.1, price=60000)
    cached = {"BTCUSDT": {"price": 65000.0, "at": _at(3), "source": "cache"}}
    live = {"BTCUSDT": {"price": 75000.0, "at": _at(3), "source": "live"}}

    with_live = await _snapshot(pid, live_prices=live, cached_prices=cached)
    assert with_live["positions"][0]["price_source"] == "live"
    assert with_live["positions"][0]["price"] == pytest.approx(75000.0)

    with_mark = await _snapshot(pid, cached_prices=cached)
    assert with_mark["positions"][0]["price_source"] == "mark"
    assert with_mark["positions"][0]["price"] == pytest.approx(70000.0)

    await allocation.update_portfolio(pid, {"marks": {}})
    with_cache = await _snapshot(pid, cached_prices=cached)
    assert with_cache["positions"][0]["price_source"] == "cache"
    assert with_cache["positions"][0]["price"] == pytest.approx(65000.0)

    with_cost = await _snapshot(pid)
    assert with_cost["positions"][0]["price_source"] == "cost"
    assert with_cost["positions"][0]["price"] == pytest.approx(60000.0)


async def test_stale_marks_and_cache_are_flagged() -> None:
    portfolio = await _portfolio(marks={"BTCUSDT": {"price": 70000.0}})
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=10000)
    await _entry(pid, "buy", _at(2), symbol="BTCUSDT", quantity=0.1, price=60000)

    config = json.loads(settings.allocation_path.read_text(encoding="utf-8"))
    config["portfolios"][0]["marks"]["BTCUSDT"]["as_of"] = "2025-01-01T00:00:00Z"
    settings.allocation_path.write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )

    snapshot = await _snapshot(pid)
    position = snapshot["positions"][0]
    assert position["price_source"] == "mark"
    assert position["price_stale"] is True
    assert any("价格已过期" in warning for warning in position["warnings"])


async def test_cash_negative_warning() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "buy", _at(1), symbol="BTCUSDT", quantity=1, price=100)
    snapshot = await _snapshot(pid)
    assert snapshot["cash"] == pytest.approx(-100.0)
    assert any("现金为负" in warning for warning in snapshot["warnings"])


async def test_empty_portfolio_snapshot() -> None:
    portfolio = await _portfolio()
    snapshot = await _snapshot(portfolio["id"])
    assert snapshot["cash"] == 0.0
    assert snapshot["positions"] == []
    assert snapshot["total_value"] == 0.0
    assert snapshot["total_return_pct"] is None
    assert any("总资产为 0" in warning for warning in snapshot["warnings"])


async def test_multiple_portfolios_are_isolated() -> None:
    usdt = await _portfolio(name="USDT 组合", currency="USDT")
    cny = await _portfolio(name="A股组合", currency="CNY")
    await _entry(usdt["id"], "deposit", _at(1), amount=1000)
    await _entry(cny["id"], "deposit", _at(1), amount=8000)

    snapshot = await allocation.get_snapshot()
    by_id = {item["id"]: item for item in snapshot["portfolios"]}
    assert by_id[usdt["id"]]["cash"] == pytest.approx(1000.0)
    assert by_id[cny["id"]]["cash"] == pytest.approx(8000.0)
    assert by_id[usdt["id"]]["currency"] == "USDT"
    assert by_id[cny["id"]]["currency"] == "CNY"


async def test_corrupt_config_and_ledger_lines(tmp_path) -> None:
    settings.allocation_path.write_text("not json", encoding="utf-8")
    with pytest.raises(allocation.AllocationError):
        await allocation.load_config()
    settings.allocation_path.unlink()

    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=100)
    with settings.allocation_ledger_path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"id": "bad"}) + "\n")
    entries = await _entries(pid)
    assert len(entries) == 1
    assert entries[0]["type"] == "deposit"


async def test_file_permissions_and_required_fields() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=100)
    assert os.stat(settings.allocation_ledger_path).st_mode & 0o777 == 0o600

    with pytest.raises(allocation.AllocationError):
        await _entry(pid, "deposit", _at(2))
    with pytest.raises(allocation.AllocationError):
        await _entry(pid, "buy", _at(2), quantity=1, price=10)
    with pytest.raises(allocation.AllocationError):
        await _entry(pid, "sell", _at(2), symbol="BTCUSDT", quantity=1, price=10)


async def test_hand_edited_lowercase_symbol_is_normalized() -> None:
    portfolio = await _portfolio()
    pid = portfolio["id"]
    await _entry(pid, "deposit", _at(1), amount=1000)
    await _entry(pid, "buy", _at(2), symbol="BTCUSDT", quantity=1, price=100)
    lines = settings.allocation_ledger_path.read_text(encoding="utf-8").splitlines()
    settings.allocation_ledger_path.write_text(
        "\n".join(line.replace('"BTCUSDT"', '"btcusdt"') for line in lines) + "\n",
        encoding="utf-8",
    )
    items = await _entries(pid)
    buy = next(item for item in items if item["type"] == "buy")
    assert buy["symbol"] == "BTCUSDT"


async def test_xirr_solution_and_no_solution() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rate = allocation.compute_xirr([(start, -1000.0), (end, 1100.0)])
    assert rate == pytest.approx(10.0, abs=0.5)
    assert allocation.compute_xirr([(start, 1000.0), (end, 1100.0)]) is None
    assert allocation.compute_xirr([]) is None


def test_infer_market_and_bitget_pair() -> None:
    assert allocation.infer_market("ETH") == "crypto"
    assert allocation.infer_market("ETHUSDT") == "crypto"
    assert allocation.infer_market("SPY") == "stock"
    assert allocation.infer_market("600519.SH") == "a_share"
    assert allocation.bitget_pair("ETH") == "ETHUSDT"
    assert allocation.bitget_pair("ETHUSDT") == "ETHUSDT"
    assert allocation.bitget_pair("ETHBTC") == "ETHBTC"


async def test_fetch_live_prices_maps_bare_ticker(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_fetch(symbol: str, granularity: str = "1min") -> dict:
        calls.append((symbol, granularity))
        return {"open_time_ms": 1735689600000, "close": 2227.0}

    monkeypatch.setattr(
        allocation.market_service, "fetch_latest_candle", fake_fetch
    )
    prices, errors = await allocation.fetch_live_prices(
        [{"symbol": "ETH", "market": "crypto"}]
    )
    assert calls == [("ETHUSDT", "1min")]
    assert prices["ETH"]["price"] == pytest.approx(2227.0)
    assert errors == []


async def test_fetch_live_prices_fetches_stock_quote(monkeypatch) -> None:
    from app.services.stock_quotes import StockQuote

    async def fake_stock(symbol: str):
        return StockQuote(
            symbol=symbol,
            price=735.29,
            at="2026-11-06T20:00:00Z",
            currency="USD",
            exchange="PCY",
            instrument_type="ETF",
        )

    monkeypatch.setattr(allocation, "fetch_stock_quote", fake_stock)
    prices, errors = await allocation.fetch_live_prices(
        [{"symbol": "SPY", "market": "stock"}]
    )
    assert errors == []
    assert prices["SPY"]["price"] == pytest.approx(735.29)
    assert prices["SPY"]["source"] == "stock"


async def test_stock_live_price_source() -> None:
    portfolio = await _portfolio(marks={"SPY": {"price": 500.0}})
    pid = portfolio["id"]
    await _entry(pid, "buy", _at(1), symbol="SPY", quantity=1, price=400)
    snapshot = await _snapshot(
        pid,
        live_prices={
            "SPY": {
                "price": 735.29,
                "at": "2026-11-06T20:00:00Z",
                "source": "stock",
            }
        },
    )
    position = snapshot["positions"][0]
    assert position["price_source"] == "stock"
    assert position["price"] == pytest.approx(735.29)


async def test_stock_position_uses_mark_and_stock_market() -> None:
    portfolio = await _portfolio(marks={"SPY": {"price": 500.0}})
    pid = portfolio["id"]
    await _entry(
        pid,
        "buy",
        _at(1),
        symbol="SPY",
        quantity=1,
        price=400,
    )
    snapshot = await _snapshot(pid)
    position = snapshot["positions"][0]
    assert position["market"] == "stock"
    assert position["price_source"] == "mark"
    assert position["price"] == pytest.approx(500.0)
    assert position["market_value"] == pytest.approx(500.0)


def test_parse_eastmoney_fx() -> None:
    quote = stock_quotes.parse_eastmoney_fx(
        {"data": {"f43": 66738, "f57": "USDCNH", "f59": 4, "f86": 1794002340}}
    )
    assert quote.rate == pytest.approx(6.6738)
    assert quote.source == "eastmoney"


async def test_fetch_usd_cny_rate_prefers_eastmoney(monkeypatch) -> None:
    async def fake_eastmoney(**kwargs):
        return FxQuote(
            pair="USD/CNY",
            rate=7.1,
            at="2025-01-03T00:00:00Z",
            source="eastmoney",
        )

    monkeypatch.setattr(stock_quotes, "fetch_eastmoney_fx", fake_eastmoney)
    quote = await stock_quotes.fetch_usd_cny_rate()
    assert quote.rate == pytest.approx(7.1)
    assert quote.source == "eastmoney"


async def test_fetch_usd_cny_rate_falls_back_to_yahoo(monkeypatch) -> None:
    async def boom(**kwargs):
        raise StockQuoteError("no eastmoney")

    async def fake_yahoo(symbol, client=None):
        assert symbol == "CNY=X"
        return StockQuote(
            symbol="CNY=X",
            price=7.25,
            at="2025-01-03T00:00:00Z",
            currency="CNY",
            exchange="FX",
            instrument_type="CURRENCY",
        )

    monkeypatch.setattr(stock_quotes, "fetch_eastmoney_fx", boom)
    monkeypatch.setattr(stock_quotes, "fetch_yahoo_quote", fake_yahoo)
    quote = await stock_quotes.fetch_usd_cny_rate()
    assert quote.rate == pytest.approx(7.25)
    assert quote.source == "yahoo"
