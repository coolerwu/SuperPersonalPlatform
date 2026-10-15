"""交易所公开行情客户端（只读）。

只访问公开 market data 接口，不接受 API key，不向交易所写入任何内容。
默认走 Binance 官方的只读行情域 data-api.binance.vision：它只提供公开行情，
不需要 key，且从境内可直连（实测生产机直连 200，而 api.binance.com 直接超时、
经代理出口会被判 HTTP 451）。需要换域名时用 `market.base_url` 覆盖。
`market.proxy` 留空时沿用 httpx 的进程代理环境变量（HTTPS_PROXY / HTTP_PROXY / ALL_PROXY）；
注意 httpx 在 macOS 上还会读系统代理设置，且不读系统代理的例外列表。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from server.domain.market import Bar, TIMEFRAME_SECONDS

DEFAULT_BASE_URL = "https://data-api.binance.vision"
DEFAULT_TIMEOUT_SECONDS = 10.0


class MarketDataError(RuntimeError):
    """行情源不可用或返回不可解析的数据。"""


@dataclass(frozen=True)
class Ticker24h:
    last_price: float
    change_pct: float
    high: float
    low: float
    volume: float


class ExchangeClient:
    """Binance 现货公开行情。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        proxy: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        kwargs: dict[str, object] = {"timeout": httpx.Timeout(timeout_seconds, connect=timeout_seconds / 2)}
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int, now_ms: int) -> list[Bar]:
        """拉取 K 线。最后一根若尚未收盘，closed=False。"""
        if timeframe not in TIMEFRAME_SECONDS:
            raise MarketDataError(f"unsupported timeframe {timeframe}")
        payload = await self._get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": timeframe, "limit": max(1, min(limit, 1000))},
        )
        if not isinstance(payload, list):
            raise MarketDataError("klines response is not a list")
        bars: list[Bar] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                open_time = int(row[0])
                close_time = int(row[6]) if len(row) > 6 else open_time
                bars.append(
                    Bar(
                        open_time=open_time,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        closed=close_time < now_ms,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise MarketDataError(f"malformed kline row for {symbol} {timeframe}") from exc
        if not bars:
            raise MarketDataError(f"no klines returned for {symbol} {timeframe}")
        return bars

    async def fetch_ticker(self, symbol: str) -> Ticker24h:
        payload = await self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise MarketDataError("ticker response is not an object")
        try:
            return Ticker24h(
                last_price=float(payload["lastPrice"]),
                change_pct=float(payload["priceChangePercent"]),
                high=float(payload["highPrice"]),
                low=float(payload["lowPrice"]),
                volume=float(payload["volume"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(f"malformed ticker for {symbol}") from exc

    async def _get(self, path: str, params: dict[str, object]) -> object:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise MarketDataError(f"market data request failed: {exc}") from exc
        if response.status_code >= 400:
            raise MarketDataError(
                f"market data request rejected: HTTP {response.status_code} {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError("market data response is not JSON") from exc
