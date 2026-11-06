from __future__ import annotations

import pandas as pd
import pytest

from app.services import chan_backtest


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    times = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    data = [
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}
        for open_, high, low, close in rows
    ]
    return pd.DataFrame(data, index=times)


def _signal(confirmed_index: int, kind: str = "1buy", price: float = 99.0) -> dict:
    time = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000) + confirmed_index * 3_600_000
    return {
        "type": kind,
        "time": time,
        "price": price,
        "stroke_index": 0,
        "reason": "test",
        "confirmed_index": confirmed_index,
        "confirmed_time": time,
    }


def test_simulate_enters_next_bar_and_hits_target() -> None:
    frame = _frame(
        [
            (100, 101, 99, 100),
            (100, 102, 99, 101),
            (101, 103, 100, 102),
            (102, 104, 101, 103),
            (103, 105, 102, 104),
            (104, 106, 103, 105),
        ]
    )
    result = chan_backtest.simulate_trades(
        frame,
        [_signal(0, price=99)],
        initial_capital=10_000,
        fee_rate=0,
        slippage_rate=0,
        stop_pct=0.01,
        risk_pct=0.01,
        r_multiple=2,
    )
    assert result["trades"] == 1
    trade = result["trades_detail"][0]
    assert trade["entry_time"] == int(pd.Timestamp("2026-01-01 01:00", tz="UTC").timestamp() * 1000)
    assert trade["entry_price"] == pytest.approx(100)
    assert trade["reason"] == "target"
    assert trade["r_multiple"] == pytest.approx(2, abs=0.01)


def test_simulate_stop_and_time_stop() -> None:
    frame = _frame(
        [
            (100, 101, 99, 100),
            (100, 101, 98, 99),
            (99, 100, 97, 98),
            (98, 99, 96, 97),
        ]
    )
    result = chan_backtest.simulate_trades(
        frame,
        [_signal(0, price=100)],
        fee_rate=0,
        slippage_rate=0,
        stop_pct=0.01,
        risk_pct=0.01,
        r_multiple=2,
    )
    assert result["trades_detail"][0]["reason"] == "stop"


def test_collect_causal_signals_uses_first_prefix(monkeypatch) -> None:
    frame = _frame([(100, 101, 99, 100)] * 8)
    point = {
        "type": "1buy",
        "time": int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000),
        "price": 99.0,
        "stroke_index": 0,
        "reason": "test",
    }

    def fake_analyze(prefix):
        if len(prefix) >= 5:
            return {"points": [point]}
        return {"points": []}

    monkeypatch.setattr(chan_backtest, "analyze_frame", fake_analyze)
    signals = chan_backtest.collect_causal_signals(frame, warmup_bars=3)
    assert len(signals) == 1
    assert signals[0]["confirmed_index"] == 4


def test_collect_causal_signals_requires_enough_bars() -> None:
    frame = _frame([(100, 101, 99, 100)] * 3)
    assert chan_backtest.collect_causal_signals(frame, warmup_bars=10) == []
