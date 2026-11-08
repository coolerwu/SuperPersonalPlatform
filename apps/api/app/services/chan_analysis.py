"""Deterministic Chan-theory structure analysis for OHLC bars.

This is intentionally a *rule-based* implementation, not an AI drawing. It
covers inclusion merging, fractals, old-style strokes (at least five merged
bars including both fractal bars), central zones, stroke-level buy/sell points
and MACD-area divergence. Segments and multi-level analysis are left for a
later version because their definitions vary.
"""

from __future__ import annotations

import bisect
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
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
DIVERGENCE_RATIO = 0.9  # area must shrink below 90% of the previous same-direction stroke
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


def _extend_stroke(stroke: dict[str, Any], fractal: dict[str, Any]) -> None:
    stroke["end_time"] = fractal["time"]
    stroke["end_price"] = fractal["price"]
    stroke["high"] = max(float(stroke["start_price"]), float(fractal["price"]))
    stroke["low"] = min(float(stroke["start_price"]), float(fractal["price"]))


def _breaks_stroke(stroke: dict[str, Any], fractal: dict[str, Any]) -> bool:
    if stroke["direction"] == "down" and fractal["type"] == "top":
        return float(fractal["price"]) > float(stroke["start_price"])
    if stroke["direction"] == "up" and fractal["type"] == "bottom":
        return float(fractal["price"]) < float(stroke["start_price"])
    return False


def build_strokes(
    fractals: list[dict[str, Any]],
    *,
    min_span: int = OLD_BI_MIN_SPAN,
) -> list[dict[str, Any]]:
    """Connect alternating fractals using the old-bi rule with stroke breaks.

    A reverse fractal that trades beyond the current stroke's starting price
    invalidates that stroke: the broken stroke is removed, the previous stroke
    extends to the new extreme, and the next opposite fractal can form a new
    stroke even if it is closer than ``min_span`` (the break itself confirms
    the reversal). Without a break, the old-bi five-bar rule still applies.
    """
    strokes: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    after_break = False
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
                    _extend_stroke(strokes[-1], fractal)
            continue
        if strokes and _breaks_stroke(strokes[-1], fractal):
            strokes.pop()
            if strokes:
                _extend_stroke(strokes[-1], fractal)
            selected = fractal
            after_break = True
            continue
        if (
            fractal["merged_index"] - selected["merged_index"] < min_span
            and not after_break
        ):
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
        after_break = False
    return strokes


def _leaves_zone(stroke: dict[str, Any], zg: float, zd: float) -> str | None:
    """Return the side a stroke ends outside the zone, if it does."""
    end = float(stroke["end_price"])
    if stroke["direction"] == "down" and end < zd:
        return "down"
    if stroke["direction"] == "up" and end > zg:
        return "up"
    return None


def _stays_outside(
    stroke: dict[str, Any], side: str, zg: float, zd: float
) -> bool:
    """Whether the stroke after a candidate fails to return into the zone."""
    end = float(stroke["end_price"])
    return end < zd if side == "down" else end > zg


def find_zones(strokes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find completed central zones: entry + three overlapping + confirmed exit.

    A zone must be entered by the stroke before its first center stroke and
    left by a stroke that ends outside ``[zd, zg]`` while the next stroke
    stays outside. A stroke that pokes out and returns is an extension, not an
    exit; this keeps a breakdown stroke from being folded into the zone it is
    leaving. A zone whose exit is not confirmed by the next stroke is not
    reported, because the structure is still unfinished at the sample end.
    After a zone the next candidate starts two strokes later, so the leaving
    stroke becomes the entry of the next zone instead of turning two zones
    into one continuous block. ``start_time`` is the entering stroke's start
    so the drawn box covers the structure's entry, while ZG/ZD still come from
    the three center strokes.
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
        exit_index: int | None = None
        while end + 1 < len(strokes):
            candidate = strokes[end + 1]
            side = _leaves_zone(candidate, zg, zd)
            if side is not None:
                after = strokes[end + 2] if end + 2 < len(strokes) else None
                if after is not None and _stays_outside(after, side, zg, zd):
                    exit_index = end + 1
                    break
            # The stroke stayed inside, or poked out and returned: extend.
            end += 1
        if exit_index is None:
            # No confirmed leaving stroke: the structure is still unfinished.
            break
        zones.append(
            {
                "start_time": int(strokes[index - 1]["start_time"]),
                "end_time": int(strokes[end]["end_time"]),
                "zg": zg,
                "zd": zd,
                "entry_stroke": index - 1,
                "start_stroke": index,
                "end_stroke": end,
                "exit_stroke": exit_index,
            }
        )
        index = end + 2
    return zones


def _macd_values(
    bars: list[dict[str, Any]],
) -> tuple[list[int], list[float], list[float], list[float]]:
    """Return bar times and MACD DIF/DEA/histogram values aligned to them."""
    close = pd.Series([float(bar["close"]) for bar in bars], dtype=float)
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = dif - dea
    times = [int(bar["time"]) for bar in bars]
    return (
        times,
        [float(value) for value in dif.tolist()],
        [float(value) for value in dea.tolist()],
        [float(value) for value in hist.tolist()],
    )


def _macd_areas(times: list[int], hist: list[float], strokes: list[dict[str, Any]]) -> list[float]:
    """Sum the MACD histogram over each stroke; used as a momentum proxy."""
    cumulative = [0.0]
    for value in hist:
        cumulative.append(cumulative[-1] + value)
    areas: list[float] = []
    for stroke in strokes:
        start = int(stroke["start_time"])
        end = int(stroke["end_time"])
        # Same window as the previous implementation: start < time <= end.
        low = bisect.bisect_right(times, start)
        high = bisect.bisect_right(times, end)
        areas.append(cumulative[high] - cumulative[low])
    return areas


def find_divergences(
    strokes: list[dict[str, Any]],
    macd_areas: list[float],
    dif_by_time: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """Find stroke-level MACD-area divergences.

    Two same-direction strokes (one stroke apart) are compared: a tradeable
    divergence needs a new price extreme while the summed MACD histogram
    shrinks below ``DIVERGENCE_RATIO`` of the previous stroke's area. This is
    the same rule that feeds the first buy/sell points; it is a simple
    momentum proxy, not a strict Chan divergence proof.
    """
    divergences: list[dict[str, Any]] = []
    for index in range(2, len(strokes)):
        stroke = strokes[index]
        previous = strokes[index - 2]
        if stroke["direction"] != previous["direction"]:
            continue
        previous_area = abs(float(macd_areas[index - 2]))
        if previous_area <= 0:
            continue
        ratio = abs(float(macd_areas[index])) / previous_area
        if ratio >= DIVERGENCE_RATIO:
            continue
        if (
            stroke["direction"] == "down"
            and float(stroke["end_price"]) < float(previous["end_price"])
        ):
            kind = "bottom"
        elif (
            stroke["direction"] == "up"
            and float(stroke["end_price"]) > float(previous["end_price"])
        ):
            kind = "top"
        else:
            continue
        entry: dict[str, Any] = {
            "kind": kind,
            "time": int(stroke["end_time"]),
            "price": float(stroke["end_price"]),
            "stroke_index": index,
            "prev_time": int(previous["end_time"]),
            "prev_price": float(previous["end_price"]),
            "area": float(macd_areas[index]),
            "prev_area": float(macd_areas[index - 2]),
            "ratio": round(ratio, 4),
        }
        if dif_by_time:
            dif = dif_by_time.get(int(stroke["end_time"]))
            prev_dif = dif_by_time.get(int(previous["end_time"]))
            if dif is not None:
                entry["dif"] = dif
            if prev_dif is not None:
                entry["prev_dif"] = prev_dif
        divergences.append(entry)
    return divergences


def find_buy_sell_points(
    strokes: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    macd_areas: list[float],
    divergences: list[dict[str, Any]] | None = None,
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

    if divergences is None:
        divergences = find_divergences(strokes, macd_areas)
    for divergence in divergences:
        index = int(divergence["stroke_index"])
        ratio = float(divergence["ratio"]) * 100
        if divergence["kind"] == "bottom":
            add("1buy", index, f"价格创新低但 MACD 面积缩小至前一同向笔的 {ratio:.0f}%（底背离）")
        elif divergence["kind"] == "top":
            add("1sell", index, f"价格创新高但 MACD 面积缩小至前一同向笔的 {ratio:.0f}%（顶背离）")

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
        exit_raw = zone.get("exit_stroke")
        if exit_raw is None:
            continue
        exit_index = int(exit_raw)
        if exit_index + 1 >= len(strokes):
            continue
        exit_stroke = strokes[exit_index]
        following = strokes[exit_index + 1]
        if exit_stroke["direction"] == "up":
            if (
                float(exit_stroke["end_price"]) > float(zone["zg"])
                and following["direction"] == "down"
                and float(following["end_price"]) > float(zone["zg"])
            ):
                add("3buy", exit_index + 1, "离开中枢后回踩不进入 ZG")
        elif exit_stroke["direction"] == "down":
            if (
                float(exit_stroke["end_price"]) < float(zone["zd"])
                and following["direction"] == "up"
                and float(following["end_price"]) < float(zone["zd"])
            ):
                add("3sell", exit_index + 1, "离开中枢后反抽不进入 ZD")

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
    macd_times, dif_values, dea_values, hist_values = _macd_values(bars)
    macd_areas = _macd_areas(macd_times, hist_values, strokes)
    divergences = find_divergences(
        strokes,
        macd_areas,
        dict(zip(macd_times, dif_values)),
    )
    points = find_buy_sell_points(strokes, zones, macd_areas, divergences)
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
        "macd": {
            "params": {"fast": MACD_FAST, "slow": MACD_SLOW, "signal": MACD_SIGNAL},
            "series": [
                {"time": time, "dif": dif, "dea": dea, "hist": hist}
                for time, dif, dea, hist in zip(
                    macd_times, dif_values, dea_values, hist_values
                )
            ],
        },
        "divergences": divergences,
        "rules": {
            "name": "程序化缠论 · 老笔",
            "bi_min_span": OLD_BI_MIN_SPAN,
            "bi_rule": (
                "顶底分型交替，至少 5 根合并 K 线（含两端分型）；"
                "反向分型越过当前笔起点时作废该笔，上一笔延伸到新极值，"
                "破笔后的第一笔不受跨度限制；未成笔分型不标注"
            ),
            "fractal_rule": "三根合并 K 线严格顶/底",
            "zone_rule": (
                "进入段 + 连续三笔重叠 + 确认离开段；"
                "离开段要收在 ZG 上方或 ZD 下方，且下一笔不回到中枢；"
                "刺穿后回到中枢的笔算延伸；"
                "ZG=三笔高点最小值，ZD=三笔低点最大值；"
                "中枢框从进入段起点画到最后一笔中枢终点"
            ),
            "point_rule": (
                "笔级别近似：1 买卖用 MACD 面积背驰，2 买卖回踩不破，"
                "3 买卖取离开中枢后的第一笔回抽且不重新进入 ZG/ZD"
            ),
            "divergence_rule": (
                f"同向笔（间隔一笔）比较：价格创新低/高但 MACD 柱面积收缩到前一同向笔的 "
                f"{int(DIVERGENCE_RATIO * 100)}% 以下，记为底/顶背离；"
                "与 1 买卖点同源，是简化动量判断，不是严格缠论背驰"
            ),
            "macd_params": {"fast": MACD_FAST, "slow": MACD_SLOW, "signal": MACD_SIGNAL},
            "segments": False,
            "buy_sell_points": True,
        },
        "warnings": warnings,
        "bars": len(bars),
        "merged_bars": len(merged),
        "stroke_count": len(strokes),
        "zone_count": len(zones),
        "point_count": len(points),
        "divergence_count": len(divergences),
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
