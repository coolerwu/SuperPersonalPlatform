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
SUPPORTED_GRANULARITIES = ("1h", "4h", "1day")


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
    """Find three-stroke overlapping central zones and extend them."""
    zones: list[dict[str, Any]] = []
    index = 0
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
        zones.append(
            {
                "start_time": int(strokes[index]["start_time"]),
                "end_time": int(strokes[end]["end_time"]),
                "zg": zg,
                "zd": zd,
                "start_stroke": index,
                "end_stroke": end,
            }
        )
        index = end + 1
    return zones


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
    warnings: list[str] = []
    if skipped:
        warnings.append(f"{skipped} 根行情为缺口或异常值，已跳过")
    if len(strokes) < 3:
        warnings.append("当前样本的笔不足 3 条，暂时没有中枢")
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
        "rules": {
            "name": "程序化缠论 · 老笔",
            "bi_min_span": OLD_BI_MIN_SPAN,
            "bi_rule": "顶底分型交替，至少 5 根合并 K 线（含两端分型）",
            "fractal_rule": "三根合并 K 线严格顶/底",
            "zone_rule": "连续三笔重叠；ZG=三笔高点最小值，ZD=三笔低点最大值",
            "segments": False,
            "buy_sell_points": False,
        },
        "warnings": warnings,
        "bars": len(bars),
        "merged_bars": len(merged),
        "stroke_count": len(strokes),
        "zone_count": len(zones),
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
    if granularity not in SUPPORTED_GRANULARITIES:
        raise ChanError("第一版只支持 1h、4h、1day")
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
