from __future__ import annotations

from app.services import chan_backtest


def test_backtest_route(client, monkeypatch) -> None:
    async def fake_run(symbol: str, granularity: str, **kwargs) -> dict:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "trades": 1,
            "total_return": 0.1,
            "warnings": [],
            "trades_detail": [],
        }

    monkeypatch.setattr(chan_backtest, "run_backtest", fake_run)
    response = client.post(
        "/api/chan/backtest",
        json={"symbol": "BTCUSDT", "granularity": "1h", "bars": 300},
    )
    assert response.status_code == 200
    assert response.json()["trades"] == 1


def test_backtest_route_rejects_bad_range(client) -> None:
    response = client.post(
        "/api/chan/backtest",
        json={"symbol": "BTCUSDT", "granularity": "1h", "bars": 10},
    )
    assert response.status_code == 422
