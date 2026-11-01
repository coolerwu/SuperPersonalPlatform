from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = START + 24 * 3600 * 1000

STRATEGY = "def strategy(df):\n    return df['close'] * 0 + 1.0\n"


def _payload(**overrides: object) -> dict:
    payload = {
        "symbol": "btcusdt",
        "granularity": "1h",
        "start_ms": START,
        "end_ms": END,
        "initial_capital": 10000,
        "fee_rate": 0.001,
        "slippage_rate": 0.0005,
        "strategy_code": STRATEGY,
    }
    payload.update(overrides)
    return payload


def test_create_list_detail_and_cancel(client: TestClient) -> None:
    created = client.post("/api/backtests", json=_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["status"] == "queued"
    backtest_id = body["id"]

    listed = client.get("/api/backtests")
    assert listed.status_code == 200
    assert any(item["id"] == backtest_id for item in listed.json())

    detail = client.get(f"/api/backtests/{backtest_id}")
    assert detail.status_code == 200
    assert detail.json()["strategy_code"] == STRATEGY

    result = client.get(f"/api/backtests/{backtest_id}/result")
    assert result.status_code == 409

    cancelled = client.post(f"/api/backtests/{backtest_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json() == {"cancelled": True}

    after = client.get(f"/api/backtests/{backtest_id}")
    assert after.json()["status"] == "cancelled"
    again = client.post(f"/api/backtests/{backtest_id}/cancel")
    assert again.json() == {"cancelled": False}


def test_meta_lists_granularities(client: TestClient) -> None:
    response = client.get("/api/backtests/meta")
    assert response.status_code == 200
    body = response.json()
    values = [item["value"] for item in body["granularities"]]
    assert "1h" in values and "1M" in values and "1min" in values
    assert body["max_bars"] > 0
    assert body["proxy_configured"] is False


def test_invalid_payloads_rejected(client: TestClient) -> None:
    bad_granularity = client.post("/api/backtests", json=_payload(granularity="1m"))
    assert bad_granularity.status_code == 422
    bad_range = client.post("/api/backtests", json=_payload(end_ms=START))
    assert bad_range.status_code == 422
    bad_symbol = client.post("/api/backtests", json=_payload(symbol="btc-usdt"))
    assert bad_symbol.status_code == 422


def test_missing_backtest_returns_404(client: TestClient) -> None:
    assert client.get("/api/backtests/nope").status_code == 404
    assert client.get("/api/backtests/nope/result").status_code == 404
    assert client.post("/api/backtests/nope/cancel").status_code == 404
