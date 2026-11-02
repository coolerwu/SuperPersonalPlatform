from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.backtest_engine import (
    BacktestError,
    compare_signal_prefix,
    downsample_lttb,
    downsample_ohlc,
    json_safe,
    run_backtest,
    validate_signals,
)
from app.services.timeframes import HOUR_MS

START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def make_frame(prices: list[float], start_ms: int = START) -> pd.DataFrame:
    index = pd.to_datetime(
        [start_ms + index * HOUR_MS for index in range(len(prices))], unit="ms", utc=True
    )
    return pd.DataFrame(
        {
            "open": prices,
            "high": [price * 1.01 for price in prices],
            "low": [price * 0.99 for price in prices],
            "close": prices,
            "volume": [1.0 for _ in prices],
        },
        index=index,
    )


def run(frame: pd.DataFrame, signals: object, **kwargs: object) -> dict:
    options = {
        "initial_capital": 10_000.0,
        "fee_rate": 0.0,
        "slippage_rate": 0.0,
        "granularity": "1h",
        "max_trades": 5000,
        "max_points": 2000,
    }
    options.update(kwargs)
    return run_backtest(frame, signals, **options)


def test_signal_is_applied_one_bar_later() -> None:
    frame = make_frame([100.0, 100.0, 100.0, 100.0, 110.0])
    signals = pd.Series([0.0, 0.0, 0.0, 0.0, 1.0], index=frame.index)
    result = run(frame, signals)
    assert result["metrics"]["total_return"] == pytest.approx(0.0)


def test_position_taken_one_bar_earlier_captures_move() -> None:
    frame = make_frame([100.0, 100.0, 100.0, 100.0, 110.0])
    signals = pd.Series([0.0, 0.0, 0.0, 1.0, 0.0], index=frame.index)
    result = run(frame, signals)
    assert result["metrics"]["total_return"] == pytest.approx(0.10)


def test_first_entry_charges_cost() -> None:
    frame = make_frame([100.0] * 5)
    result = run(
        frame,
        pd.Series([1.0] * 5, index=frame.index),
        fee_rate=0.001,
        slippage_rate=0.0005,
    )
    assert result["metrics"]["total_return"] == pytest.approx(-0.0015)
    assert result["metrics"]["trades"] == 1


def test_max_drawdown_from_equity_path() -> None:
    frame = make_frame([100.0, 120.0, 60.0, 90.0])
    signals = pd.Series([1.0] * 4, index=frame.index)
    result = run(frame, signals)
    # equity: 10000 -> 12000 -> 6000 -> 9000
    assert result["metrics"]["final_equity"] == pytest.approx(9000.0)
    assert result["metrics"]["max_drawdown"] == pytest.approx(-0.5)


def test_trades_split_by_side_and_open_trade_marked() -> None:
    frame = make_frame([100.0] * 7)
    signals = pd.Series([1.0, 1.0, -1.0, -1.0, 0.0, 1.0, 1.0], index=frame.index)
    result = run(frame, signals)
    trades = result["trades"]
    assert [trade["side"] for trade in trades] == ["long", "short", "long"]
    assert trades[0]["closed"] is True
    assert trades[1]["closed"] is True
    assert trades[-1]["closed"] is False
    assert result["trades_total"] == 3


def test_fractional_position_scales_trade_return() -> None:
    frame = make_frame([100.0, 100.0, 200.0])
    signals = pd.Series([0.5, 0.5, 0.5], index=frame.index)
    result = run(frame, signals)
    assert result["trades"][0]["return"] == pytest.approx(0.5)
    assert result["metrics"]["total_return"] == pytest.approx(0.5)


def test_nan_and_out_of_range_positions_are_warned_and_clipped() -> None:
    frame = make_frame([100.0, 101.0, 102.0, 103.0])
    signals = pd.Series([np.nan, 2.0, -3.0, 0.5], index=frame.index)
    values, warnings = validate_signals(signals, len(frame))
    assert values.tolist() == [0.0, 1.0, -1.0, 0.5]
    assert any("缺失" in warning for warning in warnings)
    assert any("裁剪" in warning for warning in warnings)


def test_wrong_signal_length_is_rejected() -> None:
    frame = make_frame([100.0, 101.0, 102.0])
    with pytest.raises(BacktestError):
        run(frame, pd.Series([1.0, 0.0]))


def test_missing_bars_warning() -> None:
    frame = make_frame([100.0, 101.0, 102.0])
    result = run(
        frame,
        pd.Series([1.0, 1.0, 1.0], index=frame.index),
        expected_bars=5,
    )
    assert any("缓存缺少 2 根" in warning for warning in result["warnings"])


def test_short_position_warning() -> None:
    frame = make_frame([100.0, 99.0, 98.0])
    result = run(frame, pd.Series([-1.0, -1.0, -1.0], index=frame.index))
    assert any("模拟做空" in warning for warning in result["warnings"])


def test_monthly_backtest_does_not_crash() -> None:
    index = pd.date_range("2024-01-01", periods=8, freq="MS", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [100.0 + index for index in range(8)],
            "high": [101.0 + index for index in range(8)],
            "low": [99.0 + index for index in range(8)],
            "close": [100.0 + index for index in range(8)],
            "volume": [1.0] * 8,
        },
        index=index,
    )
    result = run(
        frame,
        pd.Series([1.0] * 8, index=frame.index),
        granularity="1M",
    )
    assert result["metrics"]["bars"] == 8
    assert result["metrics"]["annual_return"] is not None


def test_internal_gap_is_flat_and_not_bridged() -> None:
    prices = [100.0, 100.0, 100.0, 150.0, 150.0, 150.0]
    frame = make_frame(prices)
    # Drop the fourth bar (index 3) so there is a hole between 100 and 150.
    frame = frame.drop(frame.index[3])
    signals = pd.Series([1.0] * 6, index=make_frame(prices).index)
    result = run(frame, signals)
    # The 50% jump happens across the missing bar and must not become a
    # single-bar return for a held position.
    assert result["metrics"]["total_return"] == pytest.approx(0.0)
    assert result["metrics"]["missing_bars"] == 1
    assert result["metrics"]["gap_count"] == 1
    assert any("缺少 1 根" in warning for warning in result["warnings"])


def test_warmup_signal_sets_initial_position_and_is_trimmed() -> None:
    prices = [100.0] * 5 + [110.0] * 5
    frame = make_frame(prices)
    signals = pd.Series([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    result = run(
        frame,
        signals,
        eval_start_ms=START + 5 * HOUR_MS,
        warmup_bars=5,
    )
    assert result["metrics"]["bars"] == 5
    assert result["metrics"]["warmup_bars"] == 5
    assert result["metrics"]["start_ms"] == START + 5 * HOUR_MS
    # The position decided on the last warm-up bar is held into the first
    # evaluated bar, so the 100 -> 110 move is captured.
    assert result["metrics"]["total_return"] == pytest.approx(0.10)
    assert result["trades"][0]["entry_price"] == pytest.approx(100.0)
    assert result["trades"][0]["exit_price"] == pytest.approx(110.0)


def test_warmup_shortage_is_warned() -> None:
    frame = make_frame([100.0] * 6)
    result = run(
        frame,
        pd.Series([1.0] * 6),
        eval_start_ms=START,
        warmup_bars=20,
    )
    assert result["metrics"]["warmup_bars"] == 0
    assert any("预热数据不足" in warning for warning in result["warnings"])


def test_prefix_comparison_finds_future_dependent_signals() -> None:
    full = pd.Series([0.0, 1.0, 0.0, 1.0])
    causal_prefix = pd.Series([0.0, 1.0])
    future_prefix = pd.Series([1.0, 1.0])
    assert compare_signal_prefix(full, causal_prefix) == 0
    assert compare_signal_prefix(full, future_prefix) == 1


def test_downsampling_keeps_endpoints_and_limits_points() -> None:
    times = list(range(10_000))
    values = [float(index % 7) for index in range(10_000)]
    points = downsample_lttb(times, values, 100)
    assert len(points) <= 100
    assert points[0] == [0, 0.0]
    assert points[-1] == [9999, 9999.0 % 7]

    frame = make_frame([100.0 + index for index in range(1000)])
    candles = downsample_ohlc(frame, 100)
    assert len(candles) <= 100
    assert candles[0][0] == START
    assert candles[-1][4] == pytest.approx(1099.0)


def test_json_safe_converts_unserialisable_numbers() -> None:
    value = {
        "nan": float("nan"),
        "inf": np.inf,
        "count": np.int64(3),
        "flag": np.bool_(True),
        "list": [np.float64(1.5), float("-inf")],
    }
    assert json_safe(value) == {
        "nan": None,
        "inf": None,
        "count": 3,
        "flag": True,
        "list": [1.5, None],
    }
