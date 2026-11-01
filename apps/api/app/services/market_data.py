from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import func, insert, select

from app.config import settings
from app.market_db import MarketSessionLocal
from app.market_models import MarketCandle, MarketSyncState
from app.models import utcnow
from app.services.timeframes import (
    BITGET_GRANULARITY,
    ceil_open_ms,
    count_bars,
    floor_open_ms,
    next_open_ms,
    now_ms,
    validate_granularity,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int], Awaitable[None]]


class MarketDataError(Exception):
    """User-facing market data failure with a coarse category."""

    def __init__(self, message: str, *, category: str = "data") -> None:
        super().__init__(message)
        self.category = category


@dataclass
class EnsureResult:
    symbol: str
    granularity: str
    data_start_ms: int | None
    data_end_ms: int | None
    end_open_ms: int
    rows: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class MarketStatus:
    symbol: str
    granularity: str
    rows: int
    first_ms: int | None
    last_ms: int | None
    oldest_available_ms: int | None
    last_synced_at: datetime | None
    last_error: str | None


def parse_candles(rows: list) -> list[dict]:
    """Parse Bitget's array-of-arrays candle payload into typed rows.

    Spot candles are ``[ts, open, high, low, close, baseVol, quoteVol, ...]``
    and are returned in ascending order; sort and dedupe defensively anyway.
    """
    parsed: dict[int, dict] = {}
    for row in rows or []:
        try:
            open_time = int(row[0])
            values = [float(row[index]) for index in range(1, 5)]
            base_volume = float(row[5]) if len(row) > 5 and row[5] not in (None, "") else 0.0
            quote_volume = float(row[6]) if len(row) > 6 and row[6] not in (None, "") else 0.0
        except (IndexError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (*values, base_volume, quote_volume)):
            continue
        parsed[open_time] = {
            "open_time_ms": open_time,
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "base_volume": base_volume,
            "quote_volume": quote_volume,
        }
    return [parsed[key] for key in sorted(parsed)]


class BitgetCandleClient:
    """Small async client for Bitget's public spot candle endpoints."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        proxy: str | None = None,
        timeout: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.bitget_base_url).rstrip("/")
        self.proxy = proxy if proxy is not None else settings.market_http_proxy
        self.timeout = timeout or settings.market_http_timeout_seconds
        self._client = http_client
        self._owns_client = http_client is None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                proxy=self.proxy or None,
                trust_env=True,
                headers={"User-Agent": "AgentCommandCenter/0.1"},
            )
        return self._client

    async def _throttle(self) -> None:
        rate = max(float(settings.market_rate_limit_per_second), 0.1)
        interval = 1.0 / rate
        async with self._lock:
            wait = self._last_request + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _request(self, path: str, params: dict) -> list:
        client = self._http()
        attempts = max(1, int(settings.market_max_retries) + 1)
        for attempt in range(attempts):
            await self._throttle()
            try:
                response = await client.get(f"{self.base_url}{path}", params=params)
            except httpx.ProxyError as exc:
                raise MarketDataError(
                    "行情代理不可用，请检查 MARKET_HTTP_PROXY 或服务器网络",
                    category="proxy",
                ) from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise MarketDataError(
                    "无法连接 Bitget，请检查服务器网络或 MARKET_HTTP_PROXY",
                    category="network",
                ) from exc
            except httpx.TimeoutException as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise MarketDataError(
                    "访问 Bitget 超时，请稍后重试或检查代理",
                    category="timeout",
                ) from exc
            except httpx.HTTPError as exc:
                raise MarketDataError(
                    f"访问 Bitget 失败：{type(exc).__name__}", category="network"
                ) from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
                    except ValueError:
                        delay = 0.5 * (2**attempt)
                    await asyncio.sleep(min(max(delay, 0.1), 5.0))
                    continue
                raise MarketDataError(
                    f"Bitget 暂时不可用（HTTP {response.status_code}）",
                    category="network",
                )
            if response.status_code >= 400:
                raise MarketDataError(
                    f"Bitget 返回 HTTP {response.status_code}", category="http"
                )
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise MarketDataError(
                    "Bitget 返回了无法解析的数据", category="data"
                ) from exc
            code = str(payload.get("code") or "")
            if code != "00000":
                message = str(payload.get("msg") or "未知错误")
                raise MarketDataError(f"Bitget 返回错误：{message}", category="data")
            return payload.get("data") or []
        raise MarketDataError("访问 Bitget 失败", category="network")

    async def fetch_candles(
        self,
        symbol: str,
        granularity: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        params: dict[str, object] = {
            "symbol": symbol,
            "granularity": BITGET_GRANULARITY[granularity],
            "limit": min(max(limit, 1), 1000),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        data = await self._request("/api/v2/spot/market/candles", params)
        return parse_candles(data)

    async def fetch_history(
        self,
        symbol: str,
        granularity: str,
        *,
        end_ms: int,
        limit: int = 200,
    ) -> list[dict]:
        params = {
            "symbol": symbol,
            "granularity": BITGET_GRANULARITY[granularity],
            "endTime": end_ms,
            "limit": min(max(limit, 1), 200),
        }
        data = await self._request("/api/v2/spot/market/history-candles", params)
        return parse_candles(data)


class MarketDataService:
    """Incremental local cache in front of Bitget's public candles."""

    def __init__(self, client: BitgetCandleClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> BitgetCandleClient:
        if self._client is None:
            self._client = BitgetCandleClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.close()
            self._client = None

    async def status(self, symbol: str, granularity: str) -> MarketStatus:
        symbol = symbol.strip().upper()
        validate_granularity(granularity)
        async with MarketSessionLocal() as db:
            count, first, last = (
                await db.execute(
                    select(
                        func.count(MarketCandle.open_time_ms),
                        func.min(MarketCandle.open_time_ms),
                        func.max(MarketCandle.open_time_ms),
                    ).where(
                        MarketCandle.symbol == symbol,
                        MarketCandle.granularity == granularity,
                    )
                )
            ).one()
            state = await db.get(MarketSyncState, (symbol, granularity))
        return MarketStatus(
            symbol=symbol,
            granularity=granularity,
            rows=int(count or 0),
            first_ms=first,
            last_ms=last,
            oldest_available_ms=state.oldest_available_ms if state else None,
            last_synced_at=state.last_synced_at if state else None,
            last_error=state.last_error if state else None,
        )

    async def probe(
        self, symbol: str, granularity: str
    ) -> tuple[bool, int | None, str | None]:
        symbol = symbol.strip().upper()
        validate_granularity(granularity)
        started = time.monotonic()
        try:
            rows = await self._get_client().fetch_candles(
                symbol, granularity, limit=1
            )
            ok = bool(rows)
            error = None if ok else "Bitget 返回空数据，请检查交易对和周期"
        except MarketDataError as exc:
            ok = False
            error = str(exc)
        latency = int((time.monotonic() - started) * 1000)
        return ok, latency, error

    async def ensure_data(
        self,
        symbol: str,
        granularity: str,
        start_ms: int,
        end_ms: int,
        *,
        progress: ProgressCallback | None = None,
    ) -> EnsureResult:
        symbol = symbol.strip().upper()
        validate_granularity(granularity)
        start = floor_open_ms(start_ms, granularity)
        now = now_ms()
        requested_end_open = ceil_open_ms(end_ms, granularity)
        end_open = min(requested_end_open, floor_open_ms(now, granularity))
        if end_open <= start:
            raise MarketDataError("所选区间内没有已经完成的 K 线", category="data")
        wanted = count_bars(start, end_open, granularity)
        if wanted < 2:
            raise MarketDataError("所选区间内 K 线不足 2 根", category="data")
        if wanted > settings.backtest_max_bars:
            raise MarketDataError(
                f"所选区间约 {wanted} 根 K 线，超过上限 {settings.backtest_max_bars}；"
                "请缩短区间或换更大的周期",
                category="data",
            )

        client = self._get_client()
        fetched = 0
        oldest_available: int | None = None
        try:
            cached_min, cached_max = await self._cached_range(symbol, granularity)
            if cached_min is None:
                fetched += await self._seed(client, symbol, granularity, end_open)
                cached_min, cached_max = await self._cached_range(symbol, granularity)
                if cached_min is None:
                    raise MarketDataError(
                        f"Bitget 没有 {symbol} {granularity} 的行情数据", category="data"
                    )
            if progress is not None:
                await progress(fetched)

            if cached_min > start:
                # If the cache sits entirely after the requested window, start
                # from the window's top instead of pulling everything above it.
                backward_from = min(cached_min, end_open)
                from_history, exhausted = await self._fetch_backward(
                    client, symbol, granularity, backward_from, start, progress, base=fetched
                )
                fetched += from_history
                if exhausted is not None:
                    oldest_available = exhausted

            cached_min, cached_max = await self._cached_range(symbol, granularity)
            last_wanted = _last_open_before(end_open, granularity)
            if cached_max is None or cached_max < last_wanted:
                # If the cache is entirely before the requested window, start at
                # the window instead of filling a potentially huge gap.
                cursor = (
                    cached_max
                    if cached_max is not None and cached_max >= start
                    else start
                )
                fetched += await self._fetch_forward(
                    client, symbol, granularity, cursor, end_open, progress, base=fetched
                )
        except MarketDataError as exc:
            await self._record_state(symbol, granularity, error=str(exc))
            raise
        except Exception:
            logger.exception("market data fetch failed for %s %s", symbol, granularity)
            await self._record_state(
                symbol, granularity, error="行情抓取异常，请稍后重试"
            )
            raise

        cached_min, cached_max = await self._cached_range(symbol, granularity)
        await self._record_state(
            symbol,
            granularity,
            oldest_available=oldest_available,
            newest_synced=cached_max,
        )
        data_start = max(start, cached_min) if cached_min is not None else None
        data_end = cached_max
        warnings: list[str] = []
        if requested_end_open > end_open:
            warnings.append("结束时间超过当前时间，已截断到最近一根完整 K 线")
        if data_start is None or data_start > start:
            warnings.append("Bitget 只提供从该时间开始的行情，实际回测区间已缩短")
        if data_end is None or data_end < last_wanted:
            warnings.append("缓存只覆盖到该时间，实际回测区间已缩短")
        return EnsureResult(
            symbol=symbol,
            granularity=granularity,
            data_start_ms=data_start,
            data_end_ms=data_end,
            end_open_ms=end_open,
            rows=int(fetched),
            warnings=warnings,
        )

    async def _cached_range(
        self, symbol: str, granularity: str
    ) -> tuple[int | None, int | None]:
        async with MarketSessionLocal() as db:
            first, last = (
                await db.execute(
                    select(
                        func.min(MarketCandle.open_time_ms),
                        func.max(MarketCandle.open_time_ms),
                    ).where(
                        MarketCandle.symbol == symbol,
                        MarketCandle.granularity == granularity,
                    )
                )
            ).one()
        return first, last

    async def _seed(
        self, client: BitgetCandleClient, symbol: str, granularity: str, end_open: int
    ) -> int:
        rows = await client.fetch_candles(
            symbol, granularity, end_ms=end_open - 1, limit=1000
        )
        if not rows:
            rows = await client.fetch_history(
                symbol, granularity, end_ms=end_open - 1, limit=200
            )
        if not rows:
            return 0
        await self._save(symbol, granularity, rows)
        return len(rows)

    async def _fetch_forward(
        self,
        client: BitgetCandleClient,
        symbol: str,
        granularity: str,
        cursor: int,
        end_open: int,
        progress: ProgressCallback | None,
        *,
        base: int = 0,
    ) -> int:
        fetched = 0
        last_wanted = _last_open_before(end_open, granularity)
        while cursor <= last_wanted:
            rows = await client.fetch_candles(
                symbol,
                granularity,
                start_ms=cursor,
                end_ms=end_open - 1,
                limit=1000,
            )
            if not rows:
                break
            await self._save(symbol, granularity, rows)
            fetched += len(rows)
            max_ts = max(row["open_time_ms"] for row in rows)
            if max_ts < cursor:
                break
            next_cursor = next_open_ms(max_ts, granularity)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if max_ts >= last_wanted:
                break
            if progress is not None:
                await progress(base + fetched)
        return fetched

    async def _fetch_backward(
        self,
        client: BitgetCandleClient,
        symbol: str,
        granularity: str,
        cursor: int,
        start: int,
        progress: ProgressCallback | None,
        *,
        base: int = 0,
    ) -> tuple[int, int | None]:
        fetched = 0
        while cursor > start:
            # Bitget floors endTime to the bar boundary and excludes that bar.
            # Passing ``cursor`` (the earliest cached open) returns bars strictly
            # older than it; passing ``cursor - 1`` would skip one bar per page.
            rows = await client.fetch_history(
                symbol, granularity, end_ms=cursor, limit=200
            )
            if not rows:
                return fetched, cursor
            await self._save(symbol, granularity, rows)
            fetched += len(rows)
            min_ts = min(row["open_time_ms"] for row in rows)
            if min_ts >= cursor:
                return fetched, cursor
            cursor = min_ts
            if cursor <= start:
                break
            if progress is not None:
                await progress(base + fetched)
        return fetched, None

    async def _save(self, symbol: str, granularity: str, rows: list[dict]) -> None:
        payload = [
            {
                "symbol": symbol,
                "granularity": granularity,
                **row,
                "source": "bitget",
                "updated_at": utcnow(),
            }
            for row in rows
        ]
        async with MarketSessionLocal() as db:
            for index in range(0, len(payload), 200):
                await db.execute(
                    insert(MarketCandle).prefix_with("OR REPLACE"),
                    payload[index : index + 200],
                )
            await db.commit()

    async def _record_state(
        self,
        symbol: str,
        granularity: str,
        *,
        oldest_available: int | None = None,
        newest_synced: int | None = None,
        error: str | None = None,
    ) -> None:
        async with MarketSessionLocal() as db:
            state = await db.get(MarketSyncState, (symbol, granularity))
            if state is None:
                state = MarketSyncState(symbol=symbol, granularity=granularity)
                db.add(state)
            if oldest_available is not None:
                if state.oldest_available_ms is None:
                    state.oldest_available_ms = oldest_available
                else:
                    state.oldest_available_ms = min(
                        state.oldest_available_ms, oldest_available
                    )
            if newest_synced is not None:
                state.newest_synced_ms = newest_synced
            state.last_synced_at = utcnow()
            state.last_error = error
            await db.commit()


def _last_open_before(end_open: int, granularity: str) -> int:
    """Open time of the last bar that starts before ``end_open``."""
    return floor_open_ms(end_open - 1, granularity)


market_service = MarketDataService()
