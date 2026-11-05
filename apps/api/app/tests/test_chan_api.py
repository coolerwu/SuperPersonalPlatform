from __future__ import annotations

from app.services import chan_analysis


def test_analyze_route_returns_structures(client, monkeypatch) -> None:
    async def fake_analyze(symbol: str, granularity: str, *, bars: int = 300) -> dict:
        return {
            "symbol": symbol,
            "granularity": granularity,
            "requested_bars": bars,
            "bars": 100,
            "merged_bars": 80,
            "fractals": [],
            "strokes": [],
            "zones": [],
            "warnings": [],
        }

    monkeypatch.setattr(chan_analysis, "analyze_symbol", fake_analyze)
    response = client.post(
        "/api/chan/analyze",
        json={"symbol": "BTCUSDT", "granularity": "1h", "bars": 300},
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "BTCUSDT"


def test_analyze_route_rejects_unsupported_granularity(client) -> None:
    response = client.post(
        "/api/chan/analyze",
        json={"symbol": "BTCUSDT", "granularity": "5min", "bars": 300},
    )
    assert response.status_code == 422
    assert "1h" in response.text
