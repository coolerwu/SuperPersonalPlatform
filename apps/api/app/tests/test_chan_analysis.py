from __future__ import annotations

import pandas as pd
import pytest

from app.services.chan_analysis import (
    ChanError,
    analyze_frame,
    build_strokes,
    find_buy_sell_points,
    find_divergences,
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
        _fractal("top", 15, 19),
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


def test_build_strokes_keeps_a_confirmed_down_stroke_when_price_reclaims_its_start() -> None:
    strokes = build_strokes(
        [
            _fractal("bottom", 0, 10),
            _fractal("top", 5, 20),
            _fractal("bottom", 9, 12),
            _fractal("top", 10, 25),
            _fractal("bottom", 12, 11),
        ]
    )
    # The top at 25 is one merged bar from the 12 bottom, so it is not a pen
    # endpoint and does not rewrite the confirmed down stroke; that stroke
    # extends to the lower bottom at 11 instead.
    assert [(item["direction"], item["end_price"]) for item in strokes] == [
        ("up", 20),
        ("down", 11),
    ]
    assert strokes[0]["start_time"] == 1
    assert strokes[1]["start_time"] == 6


def test_build_strokes_keeps_a_confirmed_up_stroke_when_price_reclaims_its_start() -> None:
    strokes = build_strokes(
        [
            _fractal("top", 0, 20),
            _fractal("bottom", 5, 10),
            _fractal("top", 9, 18),
            _fractal("bottom", 10, 5),
            _fractal("top", 12, 9),
        ]
    )
    # The bottom at 5 is one merged bar after the top at 18, so the confirmed
    # up stroke stays at 18; no stroke is retracted.
    assert [(item["direction"], item["end_price"]) for item in strokes] == [
        ("down", 10),
        ("up", 18),
    ]


def test_build_strokes_keeps_multiple_pens_when_price_breaks_a_pen_start() -> None:
    strokes = build_strokes(
        [
            _fractal("top", 0, 100),
            _fractal("bottom", 4, 70),
            _fractal("top", 8, 95),
            _fractal("bottom", 9, 60),
            _fractal("top", 12, 90),
            _fractal("bottom", 16, 50),
        ]
    )
    # The reversal below the rally's start does not fold the rally into one
    # long down stroke; the confirmed pens are kept.
    assert [
        (item["direction"], item["start_price"], item["end_price"])
        for item in strokes
    ] == [
        ("down", 100, 70),
        ("up", 70, 95),
        ("down", 95, 50),
    ]


def test_build_strokes_alternates_between_valid_fractals() -> None:
    strokes = build_strokes(
        [
            _fractal("bottom", 0, 10),
            _fractal("top", 5, 20),
            _fractal("bottom", 9, 12),
            _fractal("top", 10, 19),
        ]
    )
    assert [item["direction"] for item in strokes] == ["up", "down"]
    assert strokes[-1]["end_price"] == 12


def _stroke(start: int, end: int, start_price: float, end_price: float) -> dict:
    return {
        "start_time": start,
        "end_time": end,
        "start_price": start_price,
        "end_price": end_price,
        "direction": "up" if end_price > start_price else "down",
        "high": max(start_price, end_price),
        "low": min(start_price, end_price),
    }


def test_find_zones_and_extension() -> None:
    strokes = [
        _stroke(1, 2, 30, 10),  # entry
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 13),  # still overlapping: extends the zone
        _stroke(6, 7, 13, 25),  # leaves above ZG
        _stroke(7, 8, 25, 23),  # stays above ZG: confirms the exit
    ]
    zones = find_zones(strokes)
    assert len(zones) == 1
    assert zones[0]["zg"] == pytest.approx(20)
    assert zones[0]["zd"] == pytest.approx(12)
    assert zones[0]["entry_stroke"] == 0
    assert zones[0]["start_stroke"] == 1
    assert zones[0]["end_stroke"] == 4
    assert zones[0]["exit_stroke"] == 5
    # The box runs from the first center stroke's start to the leaving
    # stroke's end.
    assert zones[0]["start_time"] == strokes[1]["start_time"]
    assert zones[0]["end_time"] == strokes[5]["end_time"]


def test_zones_need_an_entry_stroke() -> None:
    # The first strokes overlap, but nothing precedes them to enter the zone.
    strokes = [
        _stroke(1, 2, 20, 10),
        _stroke(2, 3, 10, 22),
        _stroke(3, 4, 22, 12),
    ]
    assert find_zones(strokes) == []


def test_zones_need_a_leaving_stroke() -> None:
    # Still inside the zone when the sample ends: the structure is unfinished.
    strokes = [
        _stroke(1, 2, 30, 10),
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 19),
    ]
    assert find_zones(strokes) == []


def test_zones_need_a_confirmed_exit() -> None:
    # The last stroke leaves the zone, but there is no following stroke to
    # confirm that it did not just poke out and return.
    strokes = [
        _stroke(1, 2, 30, 10),
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 8),
    ]
    assert find_zones(strokes) == []


def test_breakdown_stroke_is_the_exit_not_an_extension() -> None:
    # A stroke can start inside the zone and end below ZD. That is the leaving
    # stroke, not another center stroke; the box covers it up to its end.
    strokes = [
        _stroke(1, 2, 30, 10),
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 13),
        _stroke(6, 7, 13, 20),
        _stroke(7, 8, 20, 8),
        _stroke(8, 9, 8, 11),
    ]
    zones = find_zones(strokes)
    assert len(zones) == 1
    assert zones[0]["end_stroke"] == 5
    assert zones[0]["exit_stroke"] == 6
    assert zones[0]["end_time"] == strokes[6]["end_time"]


def test_fake_breakout_extends_the_zone() -> None:
    # Poking above ZG and returning into the zone is an extension, not an exit.
    strokes = [
        _stroke(1, 2, 30, 10),
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 18),
        _stroke(6, 7, 18, 25),
        _stroke(7, 8, 25, 14),
        _stroke(8, 9, 14, 19),
        _stroke(9, 10, 19, 8),
        _stroke(10, 11, 8, 11),
    ]
    zones = find_zones(strokes)
    assert len(zones) == 1
    assert zones[0]["end_stroke"] == 7
    assert zones[0]["exit_stroke"] == 8


def test_adjacent_zones_each_have_their_own_entry() -> None:
    strokes = [
        _stroke(1, 2, 30, 10),  # entry of zone 1
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 13),  # zone 1 ends here
        _stroke(6, 7, 13, 25),  # leaves zone 1 and enters zone 2
        _stroke(7, 8, 25, 23),  # confirms the exit, entry of zone 2
        _stroke(8, 9, 23, 32),
        _stroke(9, 10, 32, 24),
        _stroke(10, 11, 24, 40),  # zone 2 ends here
        _stroke(11, 12, 40, 38),  # confirms the exit
    ]
    zones = find_zones(strokes)
    assert len(zones) == 2
    assert zones[0]["end_stroke"] == 4
    assert zones[0]["exit_stroke"] == 5
    assert zones[1]["entry_stroke"] == 5
    assert zones[1]["start_stroke"] == 6
    assert zones[1]["start_stroke"] > zones[0]["end_stroke"]
    assert zones[1]["start_time"] == strokes[zones[1]["start_stroke"]]["start_time"]
    # The leaving stroke closes one box and opens the next: no gaps.
    assert zones[0]["end_time"] == zones[1]["start_time"]
    assert zones[1]["zg"] == pytest.approx(25)
    assert zones[1]["zd"] == pytest.approx(24)


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
    assert result["macd"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
    assert len(result["macd"]["series"]) == result["bars"]
    assert all(
        set(point) == {"time", "dif", "dea", "hist"}
        for point in result["macd"]["series"]
    )
    assert isinstance(result["divergences"], list)
    assert result["divergence_count"] == len(result["divergences"])
    assert result["rules"]["divergence_rule"]


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
    zone = {"zg": 110, "zd": 100, "start_stroke": 0, "end_stroke": 2, "exit_stroke": 3}
    points = find_buy_sell_points(zone_strokes, [zone], [0, 0, 0, 0, 0])
    assert any(item["type"] == "3buy" for item in points)


def test_third_sell_uses_first_rebound_after_exit() -> None:
    strokes = [
        _stroke(1, 2, 30, 10),
        _stroke(2, 3, 10, 20),
        _stroke(3, 4, 20, 12),
        _stroke(4, 5, 12, 21),
        _stroke(5, 6, 21, 13),
        _stroke(6, 7, 13, 20),
        _stroke(7, 8, 20, 8),
        _stroke(8, 9, 8, 11),
        _stroke(9, 10, 11, 5),
    ]
    zones = find_zones(strokes)
    points = find_buy_sell_points(strokes, zones, [0] * len(strokes))
    sells = [point for point in points if point["type"] == "3sell"]
    assert len(sells) == 1
    assert sells[0]["time"] == strokes[7]["end_time"]
    assert sells[0]["price"] == pytest.approx(11)


def test_third_buy_uses_first_pullback_after_exit() -> None:
    strokes = [
        _stroke(1, 2, 10, 30),
        _stroke(2, 3, 30, 20),
        _stroke(3, 4, 20, 28),
        _stroke(4, 5, 28, 22),
        _stroke(5, 6, 22, 27),
        _stroke(6, 7, 27, 23),
        _stroke(7, 8, 23, 35),
        _stroke(8, 9, 35, 30),
        _stroke(9, 10, 30, 40),
    ]
    zones = find_zones(strokes)
    points = find_buy_sell_points(strokes, zones, [0] * len(strokes))
    buys = [point for point in points if point["type"] == "3buy"]
    assert len(buys) == 1
    assert buys[0]["time"] == strokes[7]["end_time"]
    assert buys[0]["price"] == pytest.approx(30)


def _trend_stroke(direction: str, start_time: int, end_time: int, start_price: float, end_price: float) -> dict:
    return {
        "direction": direction,
        "start_time": start_time,
        "end_time": end_time,
        "start_price": start_price,
        "end_price": end_price,
        "high": max(start_price, end_price),
        "low": min(start_price, end_price),
    }


def test_find_divergences_requires_new_extreme_and_shrinking_area() -> None:
    strokes = [
        _trend_stroke("down", 0, 1, 120, 100),
        _trend_stroke("up", 1, 2, 100, 118),
        _trend_stroke("down", 2, 3, 118, 95),
        _trend_stroke("up", 3, 4, 95, 120),
        _trend_stroke("down", 4, 5, 120, 98),
    ]
    divergences = find_divergences(strokes, [-20, 10, -5, 12, -3], {3: -1.2, 1: -0.8})
    assert len(divergences) == 1
    bottom = divergences[0]
    assert bottom["kind"] == "bottom"
    assert bottom["stroke_index"] == 2
    assert bottom["price"] == pytest.approx(95)
    assert bottom["ratio"] == pytest.approx(0.25)
    assert bottom["dif"] == pytest.approx(-1.2)
    assert bottom["prev_dif"] == pytest.approx(-0.8)

    # No new price extreme, or an even stronger MACD area: no divergence.
    no_extreme = [
        _trend_stroke("down", 0, 1, 120, 100),
        _trend_stroke("up", 1, 2, 100, 118),
        _trend_stroke("down", 2, 3, 118, 101),
    ]
    assert find_divergences(no_extreme, [-20, 10, -5]) == []
    stronger_area = [
        _trend_stroke("down", 0, 1, 120, 100),
        _trend_stroke("up", 1, 2, 100, 118),
        _trend_stroke("down", 2, 3, 118, 95),
    ]
    assert find_divergences(stronger_area, [-20, 10, -25]) == []


def test_find_divergences_finds_top_divergence() -> None:
    strokes = [
        _trend_stroke("up", 0, 1, 100, 118),
        _trend_stroke("down", 1, 2, 118, 112),
        _trend_stroke("up", 2, 3, 112, 125),
    ]
    divergences = find_divergences(strokes, [10, -8, 4])
    assert len(divergences) == 1
    assert divergences[0]["kind"] == "top"
    assert divergences[0]["ratio"] == pytest.approx(0.4)


def test_chan_supports_15min_and_week() -> None:
    from app.services.chan_analysis import SUPPORTED_GRANULARITIES

    assert "15min" in SUPPORTED_GRANULARITIES
    assert "1week" in SUPPORTED_GRANULARITIES
