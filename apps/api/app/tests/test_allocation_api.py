from __future__ import annotations

import pytest

from app.config import settings
from app.services import allocation


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

    monkeypatch.setattr(allocation, "fetch_live_prices", fake_fetch)
    refreshed = client.post("/api/allocation/refresh")
    assert refreshed.status_code == 200
    position = refreshed.json()["portfolios"][0]["positions"][0]
    assert position["price_source"] == "live"
    assert position["price"] == pytest.approx(65000.0)


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
