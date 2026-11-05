from __future__ import annotations

import pandas as pd
import pytest

from app.services.chan_analysis import (
    ChanError,
    analyze_frame,
    build_strokes,
    find_buy_sell_points,
    find_fractals,
    find_zones,
    merge_inclusions,
)


def _bar(time: int, high: float, low: float, close: float | None = None) -> dict:
    return {
        "time": time,
        "open": low,
        "high": high,
        "low": low,
        "close": high if close is None else close,
    }


def _merged(highs: list[float], lows: list[float]) -> list[dict]:
    return [
        {
            "time": index + 1,
            "start_time": index + 1,
            "end_time": index + 1,
            "open": lows[index],
            "high": highs[index],
            "low": lows[index],
            "close": highs[index],
            "start_index": index,
            "end_index": index,
        }
        for index in range(len(highs))
    ]


def _fractal(kind: str, index: int, price: float) -> dict:
    return {
        "type": kind,
        "time": index + 1,
        "price": price,
        "merged_index": index,
    }


def test_merge_inclusions_uses_trend_direction() -> None:
    up = merge_inclusions(
        [
            _bar(1, 12, 8),
            _bar(2, 13, 9),
            _bar(3, 12.5, 9.5),
        ]
    )
    assert len(up) == 2
    assert up[-1]["high"] == pytest.approx(13)
    assert up[-1]["low"] == pytest.approx(9.5)
    assert up[-1]["end_time"] == 3

    down = merge_inclusions(
        [
            _bar(1, 12, 8),
            _bar(2, 11, 7),
            _bar(3, 10.5, 7.5),
        ]
    )
    assert len(down) == 2
    assert down[-1]["high"] == pytest.approx(10.5)
    assert down[-1]["low"] == pytest.approx(7)


def test_find_fractals() -> None:
    merged = _merged([10, 14, 12, 15, 9], [8, 12, 10, 13, 7])
    fractals = find_fractals(merged)
    assert [item["type"] for item in fractals] == ["top", "bottom", "top"]
    assert fractals[0]["price"] == pytest.approx(14)

    merged = _merged([12, 10, 8, 11, 13], [8, 6, 5, 7, 9])
    fractals = find_fractals(merged)
    assert [item["type"] for item in fractals] == ["bottom"]
    assert fractals[0]["price"] == pytest.approx(5)


def test_build_strokes_old_bi_and_replacement() -> None:
    fractals = [
        _fractal("bottom", 0, 10),
        _fractal("top", 5, 20),
        _fractal("bottom", 10, 12),
        _fractal("top", 15, 22),
    ]
    strokes = build_strokes(fractals)
    assert [item["direction"] for item in strokes] == ["up", "down", "up"]
    assert strokes[0]["start_price"] == 10
    assert strokes[0]["end_price"] == 20

    # A more extreme same-type fractal moves the last stroke's endpoint.
    replacement = build_strokes(
        [
            _fractal("bottom", 0, 10),
            _fractal("top", 5, 20),
            _fractal("top", 7, 24),
            _fractal("bottom", 12, 12),
        ]
    )
    assert replacement[0]["end_price"] == 24
    assert replacement[-1]["direction"] == "down"

    # Opposite fractals too close together do not create a stroke.
    close = build_strokes([_fractal("bottom", 0, 10), _fractal("top", 2, 20)])
    assert close == []


def test_find_zones_and_extension() -> None:
    strokes = [
        {"start_time": 1, "end_time": 2, "high": 20, "low": 10},
        {"start_time": 2, "end_time": 3, "high": 22, "low": 12},
        {"start_time": 3, "end_time": 4, "high": 21, "low": 11},
        {"start_time": 4, "end_time": 5, "high": 19, "low": 13},
        {"start_time": 5, "end_time": 6, "high": 25, "low": 23},
    ]
    zones = find_zones(strokes)
    assert len(zones) == 1
    assert zones[0]["zg"] == pytest.approx(20)
    assert zones[0]["zd"] == pytest.approx(12)
    assert zones[0]["start_stroke"] == 0
    assert zones[0]["end_stroke"] == 3


def test_analyze_frame_shape_and_rules() -> None:
    times = pd.date_range("2026-01-01", periods=80, freq="h", tz="UTC")
    rows = []
    for index, timestamp in enumerate(times):
        base = 100 + (index % 10) * 2 + (index // 10) * 5
        rows.append(
            {
                "open": base,
                "high": base + 3,
                "low": base - 2,
                "close": base + 1,
                "volume": 1.0,
            }
        )
    frame = pd.DataFrame(rows, index=times)
    result = analyze_frame(frame)
    assert result["bars"] == 80
    assert result["merged_bars"] > 0
    assert result["rules"]["bi_min_span"] == 4
    assert result["rules"]["segments"] is False
    assert isinstance(result["fractals"], list)
    assert all("used" in fractal for fractal in result["fractals"])
    assert isinstance(result["strokes"], list)
    assert isinstance(result["zones"], list)
    assert isinstance(result["points"], list)
    assert result["rules"]["buy_sell_points"] is True


def test_analyze_frame_rejects_empty() -> None:
    with pytest.raises(ChanError):
        analyze_frame(pd.DataFrame())



def test_find_buy_sell_points() -> None:
    strokes = [
        {"direction": "down", "start_time": 0, "end_time": 1, "start_price": 120, "end_price": 100, "high": 120, "low": 100},
        {"direction": "up", "start_time": 1, "end_time": 2, "start_price": 100, "end_price": 118, "high": 118, "low": 100},
        {"direction": "down", "start_time": 2, "end_time": 3, "start_price": 118, "end_price": 95, "high": 118, "low": 95},
        {"direction": "up", "start_time": 3, "end_time": 4, "start_price": 95, "end_price": 120, "high": 120, "low": 95},
        {"direction": "down", "start_time": 4, "end_time": 5, "start_price": 120, "end_price": 98, "high": 120, "low": 98},
    ]
    points = find_buy_sell_points(strokes, [], [-20, 10, -5, 12, -3])
    kinds = [item["type"] for item in points]
    assert "1buy" in kinds
    assert "2buy" in kinds
    assert points[0]["type"] == "1buy"

    zone_strokes = [
        {"direction": "down", "start_time": 0, "end_time": 1, "start_price": 120, "end_price": 100, "high": 120, "low": 100},
        {"direction": "up", "start_time": 1, "end_time": 2, "start_price": 100, "end_price": 115, "high": 115, "low": 100},
        {"direction": "down", "start_time": 2, "end_time": 3, "start_price": 115, "end_price": 105, "high": 115, "low": 105},
        {"direction": "up", "start_time": 3, "end_time": 4, "start_price": 105, "end_price": 120, "high": 120, "low": 105},
        {"direction": "down", "start_time": 4, "end_time": 5, "start_price": 120, "end_price": 112, "high": 120, "low": 112},
    ]
    zone = {"zg": 110, "zd": 100, "start_stroke": 0, "end_stroke": 1}
    points = find_buy_sell_points(zone_strokes, [zone], [0, 0, 0, 0, 0])
    assert any(item["type"] == "3buy" for item in points)
