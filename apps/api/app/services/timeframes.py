"""Canonical K-line timeframes shared by the market cache and the backtest engine.

All timestamps are UTC milliseconds since the epoch. Daily, weekly and monthly
bars use Bitget's UTC-aligned granularities so bar boundaries do not depend on
the exchange's default UTC+8 alignment.
"""

from __future__ import annotations

from datetime import datetime, timezone

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS
WEEK_MS = 7 * DAY_MS

GRANULARITIES: tuple[str, ...] = (
    "1min",
    "3min",
    "5min",
    "15min",
    "30min",
    "1h",
    "4h",
    "6h",
    "12h",
    "1day",
    "1week",
    "1M",
)

# Values accepted by Bitget's public candle endpoints. The default 1day/1week/1M
# values are aligned to UTC+8, so use the explicit UTC variants instead.
BITGET_GRANULARITY: dict[str, str] = {
    "1min": "1min",
    "3min": "3min",
    "5min": "5min",
    "15min": "15min",
    "30min": "30min",
    "1h": "1h",
    "4h": "4h",
    "6h": "6h",
    "12h": "12h",
    "1day": "1Dutc",
    "1week": "1Wutc",
    "1M": "1Mutc",
}

# None means calendar months, which cannot be expressed as a fixed interval.
INTERVAL_MS: dict[str, int | None] = {
    "1min": MINUTE_MS,
    "3min": 3 * MINUTE_MS,
    "5min": 5 * MINUTE_MS,
    "15min": 15 * MINUTE_MS,
    "30min": 30 * MINUTE_MS,
    "1h": HOUR_MS,
    "4h": 4 * HOUR_MS,
    "6h": 6 * HOUR_MS,
    "12h": 12 * HOUR_MS,
    "1day": DAY_MS,
    "1week": WEEK_MS,
    "1M": None,
}

PERIODS_PER_YEAR: dict[str, int] = {
    "1min": 365 * 24 * 60,
    "3min": 365 * 24 * 20,
    "5min": 365 * 24 * 12,
    "15min": 365 * 24 * 4,
    "30min": 365 * 24 * 2,
    "1h": 365 * 24,
    "4h": 365 * 6,
    "6h": 365 * 4,
    "12h": 365 * 2,
    "1day": 365,
    "1week": 52,
    "1M": 12,
}

GRANULARITY_LABELS: dict[str, str] = {
    "1min": "1 分钟",
    "3min": "3 分钟",
    "5min": "5 分钟",
    "15min": "15 分钟",
    "30min": "30 分钟",
    "1h": "1 小时",
    "4h": "4 小时",
    "6h": "6 小时",
    "12h": "12 小时",
    "1day": "1 天",
    "1week": "1 周",
    "1M": "1 月",
}


def validate_granularity(granularity: str) -> str:
    if granularity not in GRANULARITIES:
        raise ValueError(f"unsupported granularity: {granularity}")
    return granularity


def interval_ms(granularity: str) -> int:
    validate_granularity(granularity)
    value = INTERVAL_MS[granularity]
    if value is None:
        raise ValueError(f"{granularity} has no fixed interval")
    return value


def periods_per_year(granularity: str) -> int:
    validate_granularity(granularity)
    return PERIODS_PER_YEAR[granularity]


def granularity_label(granularity: str) -> str:
    return GRANULARITY_LABELS.get(granularity, granularity)


def _to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _sub_month(value: datetime) -> datetime:
    if value.month == 1:
        return value.replace(year=value.year - 1, month=12)
    return value.replace(month=value.month - 1)


def floor_open_ms(ms: int, granularity: str) -> int:
    """Round a timestamp down to the open time of its bar."""
    validate_granularity(granularity)
    if granularity == "1M":
        return _to_ms(_month_start(_to_datetime(ms)))
    if granularity == "1week":
        day = ms // DAY_MS
        # 1970-01-01 was a Thursday; Monday is weekday 0.
        weekday = (day + 3) % 7
        return (day - weekday) * DAY_MS
    step = INTERVAL_MS[granularity]
    assert step is not None
    return (ms // step) * step


def next_open_ms(open_ms: int, granularity: str) -> int:
    """Open time of the bar that follows the one starting at ``open_ms``."""
    validate_granularity(granularity)
    if granularity == "1M":
        return _to_ms(_add_month(_to_datetime(open_ms)))
    step = INTERVAL_MS[granularity]
    assert step is not None
    return open_ms + step


def previous_open_ms(open_ms: int, granularity: str) -> int:
    """Open time of the bar immediately before the one starting at ``open_ms``."""
    validate_granularity(granularity)
    if granularity == "1M":
        return _to_ms(_sub_month(_to_datetime(open_ms)))
    step = INTERVAL_MS[granularity]
    assert step is not None
    return open_ms - step


def shift_open_ms(open_ms: int, granularity: str, bars: int) -> int:
    """Move an aligned open time by a signed number of bars."""
    cursor = open_ms
    if bars >= 0:
        for _ in range(bars):
            cursor = next_open_ms(cursor, granularity)
    else:
        for _ in range(-bars):
            cursor = previous_open_ms(cursor, granularity)
    return cursor


def build_grid_ms(start_ms: int, end_open_ms: int, granularity: str) -> list[int]:
    """Aligned bar open times in ``[start_ms, end_open_ms)``."""
    if end_open_ms <= start_ms:
        return []
    validate_granularity(granularity)
    if granularity == "1M":
        grid: list[int] = []
        cursor = start_ms
        while cursor < end_open_ms:
            grid.append(cursor)
            cursor = next_open_ms(cursor, granularity)
        return grid
    step = INTERVAL_MS[granularity]
    assert step is not None
    return list(range(start_ms, end_open_ms, step))


def ceil_open_ms(ms: int, granularity: str) -> int:
    """Round a timestamp up to the next bar open (exact boundaries stay put)."""
    floor = floor_open_ms(ms, granularity)
    if floor == ms:
        return floor
    return next_open_ms(floor, granularity)


def is_complete_bar(open_ms: int, granularity: str, now_ms: int) -> bool:
    """A bar is usable only after the next bar has started."""
    return next_open_ms(open_ms, granularity) <= now_ms


def count_bars(start_ms: int, end_ms: int, granularity: str) -> int:
    """Number of aligned bars in [start_ms, end_ms)."""
    if end_ms <= start_ms:
        return 0
    validate_granularity(granularity)
    if granularity == "1M":
        count = 0
        cursor = start_ms
        while cursor < end_ms:
            cursor = next_open_ms(cursor, granularity)
            count += 1
        return count
    step = INTERVAL_MS[granularity]
    assert step is not None
    return (end_ms - start_ms) // step


def utc_ms_to_iso(ms: int) -> str:
    return _to_datetime(ms).isoformat()


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
