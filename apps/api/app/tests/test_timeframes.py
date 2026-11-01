from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.timeframes import (
    BITGET_GRANULARITY,
    GRANULARITIES,
    ceil_open_ms,
    count_bars,
    floor_open_ms,
    granularity_label,
    is_complete_bar,
    next_open_ms,
    periods_per_year,
)


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_every_granularity_has_bitget_mapping() -> None:
    assert set(BITGET_GRANULARITY) == set(GRANULARITIES)
    assert BITGET_GRANULARITY["1day"] == "1Dutc"
    assert BITGET_GRANULARITY["1week"] == "1Wutc"
    assert BITGET_GRANULARITY["1M"] == "1Mutc"
    assert granularity_label("1M") == "1 月"
    assert granularity_label("1min") == "1 分钟"


def test_floor_and_ceil_for_intraday() -> None:
    assert floor_open_ms(_ms("2026-10-31T12:34:56"), "1h") == _ms("2026-10-31T12:00:00")
    assert ceil_open_ms(_ms("2026-10-31T12:00:00"), "1h") == _ms("2026-10-31T12:00:00")
    assert ceil_open_ms(_ms("2026-10-31T12:00:01"), "1h") == _ms("2026-10-31T13:00:00")
    assert next_open_ms(_ms("2026-10-31T12:00:00"), "4h") == _ms("2026-10-31T16:00:00")


def test_daily_bars_are_utc_aligned() -> None:
    # Bitget's default 1day candles start at UTC 16:00; the canonical value must not.
    assert floor_open_ms(_ms("2026-10-31T23:59:59"), "1day") == _ms("2026-10-31T00:00:00")
    assert next_open_ms(_ms("2026-10-31T00:00:00"), "1day") == _ms("2026-11-01T00:00:00")


def test_weekly_bars_start_on_monday_utc() -> None:
    # 2026-11-01 is a Sunday.
    assert floor_open_ms(_ms("2026-11-01T08:00:00"), "1week") == _ms("2026-10-26T00:00:00")
    assert next_open_ms(_ms("2026-10-26T00:00:00"), "1week") == _ms("2026-11-02T00:00:00")


def test_monthly_bars_use_calendar_months() -> None:
    assert floor_open_ms(_ms("2026-11-15T10:00:00"), "1M") == _ms("2026-11-01T00:00:00")
    assert next_open_ms(_ms("2026-11-01T00:00:00"), "1M") == _ms("2026-12-01T00:00:00")
    assert next_open_ms(_ms("2026-12-01T00:00:00"), "1M") == _ms("2027-01-01T00:00:00")
    assert ceil_open_ms(_ms("2026-11-01T00:00:00"), "1M") == _ms("2026-11-01T00:00:00")


def test_count_bars_and_complete_bar() -> None:
    start = _ms("2026-01-01T00:00:00")
    end = _ms("2026-01-02T00:00:00")
    assert count_bars(start, end, "1h") == 24
    assert count_bars(start, end, "1day") == 1
    month_start = _ms("2026-01-01T00:00:00")
    month_end = _ms("2027-01-01T00:00:00")
    assert count_bars(month_start, month_end, "1M") == 12
    assert is_complete_bar(start, "1day", end) is True
    assert is_complete_bar(start, "1day", start + 60_000) is False


def test_unknown_granularity_rejected() -> None:
    with pytest.raises(ValueError):
        floor_open_ms(0, "1m")
    with pytest.raises(ValueError):
        periods_per_year("2h")
