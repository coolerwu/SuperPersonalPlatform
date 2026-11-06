"""Deterministic Chan-theory structure analysis for OHLC bars.

This is intentionally a *rule-based* implementation, not an AI drawing. The
first version covers inclusion merging, fractals, old-style strokes (at least
five merged bars including both fractal bars) and central zones. Segments and
buy/sell points are left for a later version because their definitions vary.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.config import settings
from app.services.backtest_engine import load_candles
from app.services.market_data import MarketDataError, market_service
from app.services.timeframes import floor_open_ms, now_ms, shift_open_ms

MAX_BARS = 1000
MIN_BARS = 20
OLD_BI_MIN_SPAN = 4  # five merged bars inclusive
SUPPORTED_GRANULARITIES = ("15min", "1h", "4h", "1day", "1week")
GRANULARITY_ALIASES = {"15m": "15min", "1w": "1week", "1wk": "1week", "1d": "1day"}


class ChanError(ValueError):
    """A structure request that should be shown to the user verbatim."""


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _merge_one(left: dict[str, Any], right: dict[str, Any], direction: int) -> dict[str, Any]:
    if direction > 0:
        high = max(float(left["high"]), float(right["high"]))
        low = max(float(left["low"]), float(right["low"]))
    else:
        high = min(float(left["high"]), float(right["high"]))
        low = min(float(left["low"]), float(right["low"]))
    return {
        **left,
        "high": high,
        "low": low,
        "close": float(right["close"]),
        "end_time": right["end_time"],
        "end_index": right["end_index"],
    }


def _included(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        float(left["high"]) >= float(right["high"]) and float(left["low"]) <= float(right["low"])
    ) or (
        float(left["high"]) <= float(right["high"]) and float(left["low"]) >= float(right["low"])
    )


def merge_inclusions(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent inclusive bars using the prevailing direction.

    ``bars`` need ``time``, ``high``, ``low``, ``open`` and ``close``. The
    returned merged bars keep the last original bar's time as ``time`` and
    expose ``start_time``/``end_time`` for drawing the merge span.
    """
    merged: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        current = {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "time": int(bar["time"]),
            "start_time": int(bar["time"]),
            "end_time": int(bar["time"]),
            "start_index": index,
            "end_index": index,
        }
        if not merged:
            merged.append(current)
            continue
        last = merged[-1]
        if not _included(last, current):
            merged.append(current)
            continue
        if len(merged) >= 2:
            direction = 1 if float(last["high"]) >= float(merged[-2]["high"]) else -1
        else:
            direction = 1 if float(current["close"]) >= float(last["close"]) else -1
        merged[-1] = _merge_one(last, current, direction)
        # A merge can create a new inclusion with the previous merged bar.
        while len(merged) >= 2 and _included(merged[-2], merged[-1]):
            if len(merged) >= 3:
                direction = (
                    1
                    if float(merged[-2]["high"]) >= float(merged[-3]["high"])
                    else -1
                )
            else:
                direction = (
                    1
                    if float(merged[-1]["high"]) >= float(merged[-2]["high"])
                    else -1
                )
            merged[-2] = _merge_one(merged[-2], merged[-1], direction)
            merged.pop()
    return merged


def find_fractals(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find strict three-bar top and bottom fractals on merged bars."""
    fractals: list[dict[str, Any]] = []
    for index in range(1, len(merged) - 1):
        left, middle, right = merged[index - 1], merged[index], merged[index + 1]
        high, low = float(middle["high"]), float(middle["low"])
        if (
            high > float(left["high"])
            and high > float(right["high"])
            and low > float(left["low"])
            and low > float(right["low"])
        ):
            fractals.append(
                {
                    "type": "top",
                    "time": int(middle["end_time"]),
                    "price": high,
                    "merged_index": index,
                }
            )
        elif (
            low < float(left["low"])
            and low < float(right["low"])
            and high < float(left["high"])
            and high < float(right["high"])
        ):
            fractals.append(
                {
                    "type": "bottom",
                    "time": int(middle["end_time"]),
                    "price": low,
                    "merged_index": index,
                }
            )
    return fractals


def build_strokes(
    fractals: list[dict[str, Any]],
    *,
    min_span: int = OLD_BI_MIN_SPAN,
) -> list[dict[str, Any]]:
    """Connect alternating fractals using the old-bi five-bar rule."""
    strokes: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for fractal in fractals:
        if selected is None:
            selected = fractal
            continue
        same_type = fractal["type"] == selected["type"]
        if same_type:
            more_extreme = (
                fractal["price"] >= selected["price"]
                if fractal["type"] == "top"
                else fractal["price"] <= selected["price"]
            )
            if more_extreme:
                selected = fractal
                if strokes:
                    strokes[-1]["end_time"] = fractal["time"]
                    strokes[-1]["end_price"] = fractal["price"]
                    strokes[-1]["high"] = max(strokes[-1]["start_price"], fractal["price"])
                    strokes[-1]["low"] = min(strokes[-1]["start_price"], fractal["price"])
            continue
        if fractal["merged_index"] - selected["merged_index"] < min_span:
            continue
        if selected["type"] == "bottom" and fractal["price"] <= selected["price"]:
            continue
        if selected["type"] == "top" and fractal["price"] >= selected["price"]:
            continue
        strokes.append(
            {
                "start_time": selected["time"],
                "start_price": selected["price"],
                "end_time": fractal["time"],
                "end_price": fractal["price"],
                "direction": "up" if fractal["price"] > selected["price"] else "down",
                "high": max(selected["price"], fractal["price"]),
                "low": min(selected["price"], fractal["price"]),
            }
        )
        selected = fractal
    return strokes


def find_zones(strokes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find completed central zones: entry stroke + three overlapping + exit.

    A zone must be entered by the stroke before its first center stroke and
    left by a stroke outside [zd, zg]; a structure that is still overlapping
    when the data ends has no exit and is not reported. After a zone the next
    candidate starts two strokes later, so the leaving stroke becomes the
    entry of the next zone instead of turning two zones into one continuous
    block.
    """
    zones: list[dict[str, Any]] = []
    index = 1  # strokes[index - 1] is the entering stroke
    while index + 2 < len(strokes):
        group = strokes[index : index + 3]
        zg = min(float(stroke["high"]) for stroke in group)
        zd = max(float(stroke["low"]) for stroke in group)
        if zg <= zd:
            index += 1
            continue
        end = index + 2
        while end + 1 < len(strokes):
            following = strokes[end + 1]
            if float(following["low"]) > zg or float(following["high"]) < zd:
                break
            end += 1
        if end + 1 >= len(strokes):
            # Still inside the zone when the sample ends: no leaving stroke.
            break
        zones.append(
            {
                "start_time": int(strokes[index]["start_time"]),
                "end_time": int(strokes[end]["end_time"]),
                "zg": zg,
                "zd": zd,
                "entry_stroke": index - 1,
                "start_stroke": index,
                "end_stroke": end,
                "exit_stroke": end + 1,
            }
        )
        index = end + 2
    return zones


def _macd_areas(frame: pd.DataFrame, strokes: list[dict[str, Any]]) -> list[float]:
    """Sum the MACD histogram over each stroke; used as a simple divergence proxy."""
    close = frame["close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    times = [int(pd.Timestamp(timestamp).timestamp() * 1000) for timestamp in frame.index]
    values = [float(value) for value in hist.tolist()]
    areas: list[float] = []
    for stroke in strokes:
        total = 0.0
        start = int(stroke["start_time"])
        end = int(stroke["end_time"])
        for timestamp, value in zip(times, values):
            if start < timestamp <= end:
                total += value
        areas.append(total)
    return areas


def find_buy_sell_points(
    strokes: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    macd_areas: list[float],
) -> list[dict[str, Any]]:
    """Find approximate stroke-level 1/2/3 buy and sell points.

    First points use price extremum + MACD-area divergence. Second points are
    the first pullback that does not break the first point. Third points are
    the first pullback/rebound outside a central zone. This is deliberately
    labelled as an approximation: it uses strokes, not full segments.
    """
    points: list[dict[str, Any]] = []
    used: set[tuple[int, float]] = set()

    def add(kind: str, index: int, reason: str) -> None:
        stroke = strokes[index]
        key = (int(stroke["end_time"]), round(float(stroke["end_price"]), 10))
        if key in used:
            return
        used.add(key)
        points.append(
            {
                "type": kind,
                "time": int(stroke["end_time"]),
                "price": float(stroke["end_price"]),
                "stroke_index": index,
                "reason": reason,
            }
        )

    for index, stroke in enumerate(strokes):
        if index < 2:
            continue
        previous = strokes[index - 2]
        if stroke["direction"] == "down" and previous["direction"] == "down":
            if (
                float(stroke["end_price"]) < float(previous["end_price"])
                and abs(macd_areas[index]) < abs(macd_areas[index - 2]) * 0.9
            ):
                add("1buy", index, "价格创新低但 MACD 面积缩小")
        elif stroke["direction"] == "up" and previous["direction"] == "up":
            if (
                float(stroke["end_price"]) > float(previous["end_price"])
                and macd_areas[index] < macd_areas[index - 2] * 0.9
            ):
                add("1sell", index, "价格创新高但 MACD 面积缩小")

    for point in [item for item in points if item["type"] == "1buy"]:
        for index in range(int(point["stroke_index"]) + 2, len(strokes), 2):
            stroke = strokes[index]
            if stroke["direction"] != "down":
                continue
            if float(stroke["end_price"]) > float(point["price"]):
                add("2buy", index, f"1 买后回踩不破 {point['price']}")
                break

    for point in [item for item in points if item["type"] == "1sell"]:
        for index in range(int(point["stroke_index"]) + 2, len(strokes), 2):
            stroke = strokes[index]
            if stroke["direction"] != "up":
                continue
            if float(stroke["end_price"]) < float(point["price"]):
                add("2sell", index, f"1 卖后反抽不破 {point['price']}")
                break

    for zone in zones:
        start = int(zone["end_stroke"]) + 1
        for index in range(start, len(strokes) - 1):
            stroke = strokes[index]
            if stroke["direction"] == "up" and float(stroke["end_price"]) > float(zone["zg"]):
                following = strokes[index + 1]
                if (
                    following["direction"] == "down"
                    and float(following["end_price"]) > float(zone["zg"])
                ):
                    add("3buy", index + 1, "离开中枢后回踩不进入 ZG")
                break
        for index in range(start, len(strokes) - 1):
            stroke = strokes[index]
            if stroke["direction"] == "down" and float(stroke["end_price"]) < float(zone["zd"]):
                following = strokes[index + 1]
                if (
                    following["direction"] == "up"
                    and float(following["end_price"]) < float(zone["zd"])
                ):
                    add("3sell", index + 1, "离开中枢后反抽不进入 ZD")
                break

    points.sort(key=lambda item: int(item["time"]))
    return points[:40]


def analyze_frame(frame: pd.DataFrame) -> dict[str, Any]:
    """Compute Chan structures from an OHLC frame indexed by UTC time."""
    if frame.empty:
        raise ChanError("没有可分析的 K 线")
    bars: list[dict[str, Any]] = []
    skipped = 0
    for index, (timestamp, row) in enumerate(frame.iterrows()):
        values = [row["open"], row["high"], row["low"], row["close"]]
        if not all(_finite(value) for value in values):
            skipped += 1
            continue
        bars.append(
            {
                "time": int(pd.Timestamp(timestamp).timestamp() * 1000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "index": index,
            }
        )
    if len(bars) < 3:
        raise ChanError("K 线不足 3 根，无法做缠论包含处理")
    merged = merge_inclusions(bars)
    fractals = find_fractals(merged)
    strokes = build_strokes(fractals)
    zones = find_zones(strokes)
    macd_areas = _macd_areas(frame, strokes)
    points = find_buy_sell_points(strokes, zones, macd_areas)
    used_fractals = {
        (int(stroke["start_time"]), float(stroke["start_price"])) for stroke in strokes
    } | {
        (int(stroke["end_time"]), float(stroke["end_price"])) for stroke in strokes
    }
    for fractal in fractals:
        fractal["used"] = (int(fractal["time"]), float(fractal["price"])) in used_fractals
    warnings: list[str] = []
    if skipped:
        warnings.append(f"{skipped} 根行情为缺口或异常值，已跳过")
    if len(strokes) < 5:
        warnings.append("当前样本的笔不足 5 条，暂时没有完整中枢（进入段 + 三笔 + 离开段）")
    return {
        "candles": [
            {
                "time": bar["time"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
            }
            for bar in bars
        ],
        "merged": [
            {
                "time": bar["time"],
                "start_time": bar["start_time"],
                "end_time": bar["end_time"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "merged_index": index,
            }
            for index, bar in enumerate(merged)
        ],
        "fractals": fractals,
        "strokes": strokes,
        "zones": zones,
        "points": points,
        "rules": {
            "name": "程序化缠论 · 老笔",
            "bi_min_span": OLD_BI_MIN_SPAN,
            "bi_rule": "顶底分型交替，至少 5 根合并 K 线（含两端分型）",
            "fractal_rule": "三根合并 K 线严格顶/底",
            "zone_rule": (
                "进入段 + 连续三笔重叠 + 离开段；"
                "ZG=三笔高点最小值，ZD=三笔低点最大值"
            ),
            "point_rule": "笔级别近似：1 买卖用 MACD 面积背驰，2 买卖回踩不破，3 买卖离开中枢后回抽不入中枢",
            "segments": False,
            "buy_sell_points": True,
        },
        "warnings": warnings,
        "bars": len(bars),
        "merged_bars": len(merged),
        "stroke_count": len(strokes),
        "zone_count": len(zones),
        "point_count": len(points),
    }


async def analyze_symbol(
    symbol: str,
    granularity: str,
    *,
    bars: int = 300,
) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ChanError("交易对不能为空")
    granularity = GRANULARITY_ALIASES.get(str(granularity).strip().lower(), str(granularity).strip())
    if granularity not in SUPPORTED_GRANULARITIES:
        raise ChanError("只支持 15min、1h、4h、1day、1week")
    if not MIN_BARS <= bars <= MAX_BARS:
        raise ChanError(f"bars 必须在 {MIN_BARS}-{MAX_BARS} 之间")
    end_open = floor_open_ms(now_ms(), granularity)
    start_ms = shift_open_ms(end_open, granularity, -bars)
    try:
        ensure = await market_service.ensure_data(symbol, granularity, start_ms, end_open)
    except MarketDataError as exc:
        raise ChanError(str(exc)) from exc
    end_ms = ensure.end_open_ms
    frame = load_candles(
        settings.market_db_path,
        symbol,
        granularity,
        start_ms,
        end_ms,
    )
    if frame.empty:
        raise ChanError("行情缓存为空，请先检查 Bitget 连接或换一个交易对/周期")
    result = analyze_frame(frame)
    result["symbol"] = symbol
    result["granularity"] = granularity
    result["start_ms"] = int(frame.index[0].timestamp() * 1000)
    result["end_ms"] = int(frame.index[-1].timestamp() * 1000)
    result["requested_bars"] = bars
    result["warnings"] = list(ensure.warnings) + list(result.get("warnings") or [])
    return result
