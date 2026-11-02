from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.market_db import MarketSessionLocal, init_market_db
from app.market_models import MarketCandle
from app.services.market_data import (
    BitgetCandleClient,
    MarketDataError,
    MarketDataService,
    parse_candles,
)
from app.services.timeframes import HOUR_MS

START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def make_bars(start_ms: int, count: int) -> list[dict]:
    bars = []
    for index in range(count):
        price = 100.0 + index
        bars.append(
            {
                "open_time_ms": start_ms + index * HOUR_MS,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "base_volume": 10.0 + index,
                "quote_volume": 1000.0 + index,
            }
        )
    return bars


class FakeBitget:
    def __init__(self, bars: list[dict], *, error: str | None = None) -> None:
        self.bars = sorted(bars, key=lambda item: item["open_time_ms"])
        self.error = error
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append({"path": request.url.path, **params})
        if self.error is not None:
            return httpx.Response(
                200, json={"code": self.error, "msg": "参数错误", "data": None}
            )
        limit = int(params.get("limit", "1000"))
        end = int(params["endTime"]) if "endTime" in params else None
        start = int(params["startTime"]) if "startTime" in params else None
        if request.url.path.endswith("history-candles"):
            pool = [
                bar for bar in self.bars if end is None or bar["open_time_ms"] < end
            ]
            selected = pool[-limit:]
        else:
            pool = [
                bar
                for bar in self.bars
                if (start is None or bar["open_time_ms"] >= start)
                and (end is None or bar["open_time_ms"] <= end)
            ]
            selected = pool[:limit]
        data = [
            [
                str(bar["open_time_ms"]),
                str(bar["open"]),
                str(bar["high"]),
                str(bar["low"]),
                str(bar["close"]),
                str(bar["base_volume"]),
                str(bar["quote_volume"]),
                str(bar["quote_volume"]),
            ]
            for bar in selected
        ]
        return httpx.Response(200, json={"code": "00000", "msg": "success", "data": data})


@pytest.fixture(autouse=True)
async def _market_db() -> None:
    await init_market_db()


@pytest.fixture
def rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "market_rate_limit_per_second", 10000)


async def _service(fake: FakeBitget) -> tuple[MarketDataService, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    service = MarketDataService(BitgetCandleClient(http_client=http))
    return service, http


async def test_ensure_data_fetches_once_and_reuses_cache(rate_limit: None) -> None:
    symbol = "CACHE1USDT"
    bars = make_bars(START, 96)
    fake = FakeBitget(bars)
    service, http = await _service(fake)
    try:
        end = START + 96 * HOUR_MS
        result = await service.ensure_data(symbol, "1h", START, end)
        assert result.data_start_ms == START
        assert result.data_end_ms == end - HOUR_MS
        assert result.rows == 96
        assert result.warnings == []
        requests_after_first = len(fake.requests)

        second = await service.ensure_data(symbol, "1h", START, end)
        assert second.data_start_ms == START
        assert second.data_end_ms == end - HOUR_MS
        assert len(fake.requests) == requests_after_first

        status = await service.status(symbol, "1h")
        assert status.rows == 96
        assert status.first_ms == START
        assert status.last_ms == end - HOUR_MS
    finally:
        await http.aclose()


async def test_ensure_data_pages_backwards_beyond_one_thousand_bars(
    rate_limit: None,
) -> None:
    symbol = "PAGEUSDT"
    bars = make_bars(START, 2500)
    fake = FakeBitget(bars)
    service, http = await _service(fake)
    try:
        end = START + 2500 * HOUR_MS
        result = await service.ensure_data(symbol, "1h", START, end)
        assert result.data_start_ms == START
        assert result.data_end_ms == end - HOUR_MS
        assert len(fake.requests) > 2
        status = await service.status(symbol, "1h")
        assert status.rows == 2500
        assert result.warnings == []
    finally:
        await http.aclose()


async def test_ensure_data_only_fetches_new_head(rate_limit: None) -> None:
    symbol = "HEADUSDT"
    bars = make_bars(START, 48)
    fake = FakeBitget(bars)
    service, http = await _service(fake)
    try:
        first_end = START + 48 * HOUR_MS
        await service.ensure_data(symbol, "1h", START, first_end)
        requests_after_first = len(fake.requests)

        fake.bars = make_bars(START, 72)
        new_end = START + 72 * HOUR_MS
        result = await service.ensure_data(symbol, "1h", START, new_end)
        assert result.data_end_ms == new_end - HOUR_MS
        assert len(fake.requests) > requests_after_first
        assert all(
            request.get("startTime") is not None
            for request in fake.requests[requests_after_first:]
        )
        status = await service.status(symbol, "1h")
        assert status.rows == 72
    finally:
        await http.aclose()


async def test_bitget_error_is_recorded_and_raised(rate_limit: None) -> None:
    symbol = "ERRORUSDT"
    fake = FakeBitget([], error="400171")
    service, http = await _service(fake)
    try:
        with pytest.raises(MarketDataError) as excinfo:
            await service.ensure_data(symbol, "1h", START, START + 10 * HOUR_MS)
        assert "Bitget 返回错误" in str(excinfo.value)
        status = await service.status(symbol, "1h")
        assert status.last_error is not None
    finally:
        await http.aclose()


async def test_max_bars_limit_rejects_before_fetching(
    rate_limit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "backtest_max_bars", 10)
    fake = FakeBitget(make_bars(START, 48))
    service, http = await _service(fake)
    try:
        with pytest.raises(MarketDataError) as excinfo:
            await service.ensure_data("LIMITUSDT", "1h", START, START + 48 * HOUR_MS)
        assert "超过上限" in str(excinfo.value)
        assert fake.requests == []
    finally:
        await http.aclose()


async def test_stale_cache_before_window_does_not_fill_gap(rate_limit: None) -> None:
    symbol = "STALEUSDT"
    stale = START - 10_000 * HOUR_MS
    async with MarketSessionLocal() as db:
        db.add(
            MarketCandle(
                symbol=symbol,
                granularity="1h",
                open_time_ms=stale,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                base_volume=1.0,
                quote_volume=1.0,
            )
        )
        await db.commit()
    fake = FakeBitget(make_bars(START, 24))
    service, http = await _service(fake)
    try:
        result = await service.ensure_data(
            symbol, "1h", START, START + 24 * HOUR_MS
        )
        assert result.data_start_ms == START
        candle_requests = [
            request for request in fake.requests if request["path"].endswith("/candles")
        ]
        assert candle_requests
        assert all(int(request["startTime"]) >= START for request in candle_requests)
    finally:
        await http.aclose()


async def test_ensure_data_fetches_warmup_before_start(rate_limit: None) -> None:
    symbol = "WARMUPUSDT"
    bars = make_bars(START - 48 * HOUR_MS, 96)
    fake = FakeBitget(bars)
    service, http = await _service(fake)
    try:
        end = START + 48 * HOUR_MS
        result = await service.ensure_data(
            symbol, "1h", START, end, warmup_bars=24
        )
        assert result.data_start_ms == START - 24 * HOUR_MS
        assert result.data_end_ms == end - HOUR_MS
    finally:
        await http.aclose()


async def test_ensure_data_repairs_internal_gap(rate_limit: None) -> None:
    symbol = "GAPUSDT"
    bars = make_bars(START, 48)
    fake = FakeBitget(bars)
    service, http = await _service(fake)
    end = START + 48 * HOUR_MS
    try:
        await service.ensure_data(symbol, "1h", START, end)
        missing_open = START + 20 * HOUR_MS
        async with MarketSessionLocal() as db:
            await db.execute(
                delete(MarketCandle).where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.granularity == "1h",
                    MarketCandle.open_time_ms == missing_open,
                )
            )
            await db.commit()

        repaired = await service.ensure_data(symbol, "1h", START, end)
        assert repaired.rows >= 1
        status = await service.status(symbol, "1h")
        assert status.rows == 48
        async with MarketSessionLocal() as db:
            rows = (
                await db.scalars(
                    select(MarketCandle).where(
                        MarketCandle.symbol == symbol,
                        MarketCandle.granularity == "1h",
                        MarketCandle.open_time_ms == missing_open,
                    )
                )
            ).all()
        assert len(rows) == 1
    finally:
        await http.aclose()


async def test_gap_repair_failure_keeps_cached_window(rate_limit: None) -> None:
    symbol = "REPAIRFAILUSDT"
    bars = make_bars(START, 48)
    service, http = await _service(FakeBitget(bars))
    end = START + 48 * HOUR_MS
    try:
        await service.ensure_data(symbol, "1h", START, end)
    finally:
        await http.aclose()
    missing_open = START + 20 * HOUR_MS
    async with MarketSessionLocal() as db:
        await db.execute(
            delete(MarketCandle).where(
                MarketCandle.symbol == symbol,
                MarketCandle.granularity == "1h",
                MarketCandle.open_time_ms == missing_open,
            )
        )
        await db.commit()

    failing, http = await _service(FakeBitget(bars, error="40004"))
    try:
        result = await failing.ensure_data(symbol, "1h", START, end)
        assert any("缺口补齐失败" in warning for warning in result.warnings)
        status = await failing.status(symbol, "1h")
        assert status.rows == 47
    finally:
        await http.aclose()


async def test_probe_success_and_failure(rate_limit: None) -> None:
    fake = FakeBitget(make_bars(START, 2))
    service, http = await _service(fake)
    try:
        ok, latency, error = await service.probe("PROBEUSDT", "1h")
        assert ok is True
        assert error is None
        assert latency is not None
    finally:
        await http.aclose()

    failing_http = httpx.AsyncClient(
        transport=httpx.MockTransport(FakeBitget([], error="400171").handler)
    )
    failing = MarketDataService(BitgetCandleClient(http_client=failing_http))
    try:
        ok, _latency, error = await failing.probe("PROBEUSDT", "1h")
        assert ok is False
        assert error is not None
    finally:
        await failing_http.aclose()


async def test_parse_candles_sorts_dedupes_and_skips_invalid() -> None:
    rows = [
        ["2000", "2", "3", "1", "2.5", "10", "20"],
        ["1000", "1", "2", "0.5", "1.5", "5", "8"],
        ["2000", "2.1", "3.1", "1.1", "2.6", "11", "21"],
        ["bad", "1", "2", "3", "4", "5", "6"],
    ]
    parsed = parse_candles(rows)
    assert [row["open_time_ms"] for row in parsed] == [1000, 2000]
    assert parsed[1]["open"] == 2.1
    assert parsed[1]["quote_volume"] == 21
