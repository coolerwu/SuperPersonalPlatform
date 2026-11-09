from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import chan_analysis
from app.services.chan_analysis import ChanError, parse_date_window


def _ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_analyze_route_returns_structures(client, monkeypatch) -> None:
    captured: dict = {}

    async def fake_analyze(
        symbol: str,
        granularity: str,
        *,
        bars: int = 500,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> dict:
        captured.update(start_ms=start_ms, end_ms=end_ms)
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
        json={
            "symbol": "BTCUSDT",
            "granularity": "1h",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
        },
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "BTCUSDT"
    assert captured["start_ms"] == _ms("2026-10-01T00:00:00")
    # The end date is inclusive, so the exclusive bound is the next midnight.
    assert captured["end_ms"] == _ms("2026-11-01T00:00:00")


def test_parse_date_window_accepts_iso_and_rejects_bad_text() -> None:
    start_ms, end_ms = parse_date_window("2026-10-01T08:00:00", "2026-10-02T08:00:00")
    assert start_ms == _ms("2026-10-01T08:00:00")
    assert end_ms == _ms("2026-10-02T08:00:00")
    assert parse_date_window(None, "") == (None, None)
    with pytest.raises(ChanError):
        parse_date_window("10/01/2026", None)


def test_analyze_route_rejects_unsupported_granularity(client) -> None:
    response = client.post(
        "/api/chan/analyze",
        json={"symbol": "BTCUSDT", "granularity": "5min", "bars": 300},
    )
    assert response.status_code == 422
    assert "1h" in response.text
