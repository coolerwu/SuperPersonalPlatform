from __future__ import annotations

import json

import pytest

from app.config import settings
from app.services import allocation
from app.services.stock_quotes import FxQuote, StockQuote, StockQuoteError


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _portfolio(**overrides):
    payload = {
        "name": "长期核心",
        "currency": "USDT",
        "cash_target_pct": 20.0,
        "rebalance_band_pct": 5.0,
        "targets": {"BTCUSDT": 50.0},
        "marks": {},
        "note": "",
    }
    payload.update(overrides)
    return payload


def _deposit(amount: float = 10000.0, at: str = "2025-01-01T00:00:00Z") -> dict:
    return {"type": "deposit", "at": at, "amount": amount}


def _buy(**overrides) -> dict:
    payload = {
        "type": "buy",
        "at": "2025-01-02T00:00:00Z",
        "symbol": "BTCUSDT",
        "quantity": 0.1,
        "price": 60000.0,
        "fees": 0.0,
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides) -> dict:
    response = client.post("/api/allocation/portfolios", json=_portfolio(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _add_entry(client, portfolio_id: str, payload: dict) -> dict:
    response = client.post(
        f"/api/allocation/portfolios/{portfolio_id}/entries", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_portfolio_deposit_buy_and_snapshot(client) -> None:
    empty = client.get("/api/allocation")
    assert empty.status_code == 200
    assert empty.json()["portfolios"] == []

    portfolio = _create(client)
    portfolio_id = portfolio["id"]
    assert portfolio["currency"] == "USDT"
    assert portfolio["targets"] == {"BTCUSDT": 50.0}

    _add_entry(client, portfolio_id, _deposit())
    buy = _add_entry(client, portfolio_id, _buy())

    snapshot = client.get("/api/allocation")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert len(body["portfolios"]) == 1
    item = body["portfolios"][0]
    assert item["cash"] == pytest.approx(4000.0)
    assert item["total_value"] == pytest.approx(10000.0)
    position = item["positions"][0]
    assert position["symbol"] == "BTCUSDT"
    assert position["quantity"] == pytest.approx(0.1)
    assert position["market_value"] == pytest.approx(6000.0)
    assert position["price_source"] == "cost"
    assert position["weight_pct"] == pytest.approx(60.0)
    assert position["target_pct"] == pytest.approx(50.0)
    assert position["drift_pp"] == pytest.approx(10.0)
    assert position["rebalance_needed"] is True
    assert item["cash_weight_pct"] == pytest.approx(40.0)
    assert item["target_sum_pct"] == pytest.approx(70.0)
    assert any("未分配" in warning for warning in item["warnings"])

    entries = client.get(f"/api/allocation/portfolios/{portfolio_id}/entries")
    assert entries.status_code == 200
    assert entries.json()["total"] == 2
    assert entries.json()["items"][0]["type"] == "buy"
    assert entries.json()["items"][0]["amount"] == pytest.approx(6000.0)

    updated = client.put(
        f"/api/allocation/portfolios/{portfolio_id}/entries/{buy['id']}",
        json={"note": "补记"},
    )
    assert updated.status_code == 200
    assert updated.json()["note"] == "补记"

    sold = _add_entry(
        client,
        portfolio_id,
        {
            "type": "sell",
            "at": "2025-01-03T00:00:00Z",
            "symbol": "BTCUSDT",
            "quantity": 0.05,
            "price": 65000.0,
            "fees": 1.0,
        },
    )
    assert sold["type"] == "sell"


def test_portfolio_update_delete_and_conflicts(client) -> None:
    portfolio = _create(client)
    portfolio_id = portfolio["id"]
    _add_entry(client, portfolio_id, _deposit())

    blocked = client.delete(f"/api/allocation/portfolios/{portfolio_id}")
    assert blocked.status_code == 409

    updated = client.put(
        f"/api/allocation/portfolios/{portfolio_id}",
        json={"name": "核心组合", "targets": {"BTCUSDT": 60.0, "ETHUSDT": 40.0}},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "核心组合"
    assert updated.json()["targets"] == {"BTCUSDT": 60.0, "ETHUSDT": 40.0}

    changed_currency = client.put(
        f"/api/allocation/portfolios/{portfolio_id}", json={"currency": "CNY"}
    )
    assert changed_currency.status_code == 422

    entries = client.get(
        f"/api/allocation/portfolios/{portfolio_id}/entries"
    ).json()["items"]
    for entry in entries:
        deleted = client.delete(
            f"/api/allocation/portfolios/{portfolio_id}/entries/{entry['id']}"
        )
        assert deleted.status_code == 204

    removed = client.delete(f"/api/allocation/portfolios/{portfolio_id}")
    assert removed.status_code == 204
    assert client.get("/api/allocation").json()["portfolios"] == []


def test_oversell_and_validation_errors(client) -> None:
    portfolio = _create(client)
    portfolio_id = portfolio["id"]
    _add_entry(client, portfolio_id, _deposit())
    _add_entry(client, portfolio_id, _buy())

    oversell = client.post(
        f"/api/allocation/portfolios/{portfolio_id}/entries",
        json={
            "type": "sell",
            "at": "2025-01-03T00:00:00Z",
            "symbol": "BTCUSDT",
            "quantity": 0.2,
            "price": 65000.0,
        },
    )
    assert oversell.status_code == 422
    assert "超过当时持仓" in oversell.json()["detail"]

    bad_target = client.post(
        "/api/allocation/portfolios",
        json=_portfolio(targets={"BTCUSDT": 120.0}),
    )
    assert bad_target.status_code == 422

    bad_cash = client.post(
        "/api/allocation/portfolios",
        json=_portfolio(cash_target_pct=120.0),
    )
    assert bad_cash.status_code == 422

    bad_type = client.post(
        f"/api/allocation/portfolios/{portfolio_id}/entries",
        json={"type": "swap", "at": "2025-01-03T00:00:00Z", "amount": 10},
    )
    assert bad_type.status_code == 422

    missing = client.get("/api/allocation/portfolios/pf_missing00/entries")
    assert missing.status_code == 404


def test_get_does_not_fetch_prices_and_refresh_does(client, monkeypatch) -> None:
    portfolio = _create(client)
    portfolio_id = portfolio["id"]
    _add_entry(client, portfolio_id, _deposit())
    _add_entry(client, portfolio_id, _buy())

    async def boom(_items):
        raise AssertionError("GET must not fetch live prices")

    monkeypatch.setattr(allocation, "fetch_live_prices", boom)
    assert client.get("/api/allocation").status_code == 200

    async def fake_fetch(_items):
        return (
            {
                "BTCUSDT": {
                    "price": 65000.0,
                    "at": "2025-01-03T00:00:00Z",
                    "source": "live",
                }
            },
            [],
        )

    async def fake_fx():
        return FxQuote(
            pair="USD/CNY",
            rate=7.2,
            at="2025-01-03T00:00:00Z",
            source="eastmoney",
        )

    monkeypatch.setattr(allocation, "fetch_live_prices", fake_fetch)
    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", fake_fx)
    refreshed = client.post("/api/allocation/refresh")
    assert refreshed.status_code == 200
    position = refreshed.json()["portfolios"][0]["positions"][0]
    assert position["price_source"] == "live"
    assert position["price"] == pytest.approx(65000.0)
    assert refreshed.json()["fx"]["usd_cny"] == pytest.approx(7.2)
    assert refreshed.json()["fx"]["source"] == "eastmoney"


def test_overview_converts_usdt_and_cny(client, monkeypatch) -> None:
    usdt = _create(client, name="USDT 组合", currency="USDT")
    cny = _create(
        client,
        name="A股组合",
        currency="CNY",
        targets={},
        cash_target_pct=None,
    )
    _add_entry(client, usdt["id"], _deposit(1000))
    _add_entry(client, cny["id"], _deposit(7200))

    async def no_prices(_items):
        return {}, []

    async def fake_fx():
        return FxQuote(
            pair="USD/CNY",
            rate=7.2,
            at="2025-01-03T00:00:00Z",
            source="eastmoney",
        )

    monkeypatch.setattr(allocation, "fetch_live_prices", no_prices)
    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", fake_fx)
    assert client.post("/api/allocation/refresh").status_code == 200

    body = client.get("/api/allocation").json()
    overview = body["overview"]
    assert overview["display_currency"] == "USD"
    assert overview["total_value"] == pytest.approx(2000.0)
    assert overview["cash"] == pytest.approx(2000.0)
    rows = {row["currency"]: row for row in overview["portfolios"]}
    assert rows["USDT"]["total_value_usd"] == pytest.approx(1000.0)
    assert rows["CNY"]["total_value_usd"] == pytest.approx(1000.0)
    assert rows["USDT"]["weight_pct"] == pytest.approx(50.0)


def test_refresh_keeps_previous_fx_on_failure(client, monkeypatch) -> None:
    portfolio = _create(client)
    _add_entry(client, portfolio["id"], _deposit(1000))

    async def no_prices(_items):
        return {}, []

    async def good_fx():
        return FxQuote(
            pair="USD/CNY",
            rate=7.1,
            at="2025-01-03T00:00:00Z",
            source="eastmoney",
        )

    async def bad_fx():
        raise StockQuoteError("boom")

    monkeypatch.setattr(allocation, "fetch_live_prices", no_prices)
    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", good_fx)
    first = client.post("/api/allocation/refresh").json()
    assert first["fx"]["usd_cny"] == pytest.approx(7.1)

    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", bad_fx)
    second = client.post("/api/allocation/refresh").json()
    assert second["fx"]["usd_cny"] == pytest.approx(7.1)
    assert "boom" in second["fx_error"]


def test_overview_excludes_unsupported_currency(client) -> None:
    usdt = _create(client, name="USDT 组合")
    hkd = _create(
        client,
        name="HKD 组合",
        currency="HKD",
        targets={},
        cash_target_pct=None,
    )
    _add_entry(client, usdt["id"], _deposit(1000))
    _add_entry(client, hkd["id"], _deposit(5000))

    body = client.get("/api/allocation").json()
    overview = body["overview"]
    assert [row["currency"] for row in overview["portfolios"]] == ["USDT"]
    assert overview["total_value"] == pytest.approx(1000.0)
    assert any("HKD" in warning for warning in overview["warnings"])


def test_legacy_config_without_fx_uses_default(client) -> None:
    portfolio = _create(client)
    _add_entry(client, portfolio["id"], _deposit(1000))

    config = json.loads(settings.allocation_path.read_text(encoding="utf-8"))
    config.pop("fx", None)
    settings.allocation_path.write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )

    body = client.get("/api/allocation").json()
    assert body["fx"]["usd_cny"] == pytest.approx(7.2)
    assert body["fx"]["source"] == "default"
    assert any(
        "尚未获取实时汇率" in warning
        for warning in body["overview"]["warnings"]
    )


def test_multiple_portfolios_and_pagination(client) -> None:
    usdt = _create(client, name="USDT 组合", currency="USDT")
    cny = _create(client, name="A股组合", currency="CNY")
    _add_entry(client, usdt["id"], _deposit(1000))
    _add_entry(client, usdt["id"], _buy(at="2025-01-02T00:00:00Z"))
    _add_entry(client, cny["id"], _deposit(8000))

    body = client.get("/api/allocation").json()
    by_id = {item["id"]: item for item in body["portfolios"]}
    assert by_id[usdt["id"]]["cash"] == pytest.approx(-5000.0)
    assert by_id[cny["id"]]["cash"] == pytest.approx(8000.0)

    page = client.get(
        f"/api/allocation/portfolios/{usdt['id']}/entries",
        params={"limit": 1, "offset": 0},
    ).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["limit"] == 1


def test_overview_merges_same_symbol_across_portfolios(client, monkeypatch) -> None:
    first = _create(client, name="USDT 组合 A")
    second = _create(client, name="USDT 组合 B")
    _add_entry(client, first["id"], _deposit(10000))
    _add_entry(
        client,
        first["id"],
        _buy(symbol="ETHUSDT", quantity=1, price=2000),
    )
    _add_entry(client, second["id"], _deposit(10000))
    _add_entry(
        client,
        second["id"],
        _buy(symbol="ETHUSDT", quantity=1, price=2500),
    )

    async def fake_fetch(_items):
        return (
            {
                "ETHUSDT": {
                    "price": 2200.0,
                    "at": "2025-01-03T00:00:00Z",
                    "source": "live",
                }
            },
            [],
        )

    async def fake_fx():
        return FxQuote(
            pair="USD/CNY",
            rate=7.2,
            at="2025-01-03T00:00:00Z",
            source="eastmoney",
        )

    monkeypatch.setattr(allocation, "fetch_live_prices", fake_fetch)
    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", fake_fx)
    refreshed = client.post("/api/allocation/refresh")
    assert refreshed.status_code == 200

    overview = refreshed.json()["overview"]
    assert len(overview["positions"]) == 1
    position = overview["positions"][0]
    assert position["symbol"] == "ETHUSDT"
    assert position["quantity"] == pytest.approx(2.0)
    assert position["market_value_usd"] == pytest.approx(4400.0)
    assert position["price_usd"] == pytest.approx(2200.0)
    assert position["avg_cost_usd"] == pytest.approx(2250.0)
    assert position["unrealized_pnl_usd"] == pytest.approx(-100.0)
    assert position["portfolio_count"] == 2
    assert position["weight_pct"] == pytest.approx(
        4400.0 / overview["total_value"] * 100
    )


def test_fixed_income_accrual_in_snapshot(client) -> None:
    portfolio = _create(
        client,
        name="固收组合",
        targets={},
        cash_target_pct=None,
        accruals={
            "UST4": {
                "annual_rate": 10,
                "start": "2025-01-01",
                "compound": False,
            }
        },
    )
    _add_entry(client, portfolio["id"], _deposit(10000))
    _add_entry(
        client,
        portfolio["id"],
        {
            "type": "buy",
            "at": "2025-01-01T00:00:00Z",
            "symbol": "UST4",
            "quantity": 1000,
            "price": 1,
            "fees": 0,
        },
    )

    position = client.get("/api/allocation").json()["portfolios"][0]["positions"][0]
    assert position["price_source"] == "accrual"
    assert position["market_value"] > 1000
    assert position["unrealized_pnl"] > 0


def test_invalid_accrual_rejected(client) -> None:
    payload = _portfolio(
        accruals={"UST4": {"annual_rate": 10, "start": "not-a-date"}}
    )
    response = client.post("/api/allocation/portfolios", json=payload)
    assert response.status_code == 422


def test_refresh_uses_fund_nav_for_a_share_funds(client, monkeypatch) -> None:
    portfolio = _create(
        client,
        name="基金组合",
        currency="CNY",
        targets={},
        cash_target_pct=None,
    )
    _add_entry(client, portfolio["id"], _deposit(10000))
    _add_entry(
        client,
        portfolio["id"],
        {
            "type": "buy",
            "at": "2025-01-02T00:00:00Z",
            "symbol": "000307",
            "quantity": 1000,
            "price": 2.5,
            "fees": 0,
        },
    )

    async def fake_nav(symbol, client=None):
        return StockQuote(
            symbol=symbol,
            price=2.8456,
            at="2026-11-06T00:00:00Z",
            currency="CNY",
            exchange="FUND",
            instrument_type="FUND",
        )

    async def fake_fx():
        return FxQuote(
            pair="USD/CNY",
            rate=7.2,
            at="2026-11-06T00:00:00Z",
            source="eastmoney",
        )

    monkeypatch.setattr(allocation, "fetch_fund_nav", fake_nav)
    monkeypatch.setattr(allocation, "fetch_usd_cny_rate", fake_fx)
    body = client.post("/api/allocation/refresh").json()
    position = body["portfolios"][0]["positions"][0]
    assert position["price_source"] == "fund"
    assert position["price"] == pytest.approx(2.8456)
