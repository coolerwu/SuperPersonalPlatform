"""交易所公开行情客户端：多源兜底，只读。

只访问公开 market data 接口，不接受 API key，不向交易所写入任何内容。
`MarketFeed` 按配置顺序尝试多个来源，第一个成功的接管并保持粘性
（只有当前源真的失败才轮到下一个），全部失败时由调用方保留上一份快照。

交易所域名与可达性约束见 `docs/project-architecture.md`：生产网里
Binance 只有 `data-api.binance.vision` 两条路都通，Gate 可直连。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

from server.domain.market import Bar, MarketSource, TIMEFRAME_SECONDS

DEFAULT_TIMEOUT_SECONDS = 10.0

# 各家默认域名。Binance 用官方只读行情域：实测生产网直连 api.binance.com 不通、
# 经局域网代理被判 451，只有这个域名在两条路上都是 200。
DEFAULT_BASE_URLS: dict[str, str] = {
    "binance": "https://data-api.binance.vision",
    "gate": "https://api.gateio.ws",
}

# 平台内部统一用 1m/5m/15m/30m/1h/4h/1d；目前两家原生写法与之一致，
# 仍然显式列出来，加新交易所时差异就在这一处。
NATIVE_INTERVALS: dict[str, dict[str, str]] = {
    "binance": {tf: tf for tf in TIMEFRAME_SECONDS},
    "gate": {tf: tf for tf in TIMEFRAME_SECONDS},
}


class MarketDataError(RuntimeError):
    """行情源不可用或返回不可解析的数据。"""


@dataclass(frozen=True)
class Ticker24h:
    last_price: float
    change_pct: float
    high: float
    low: float
    volume: float


@dataclass(frozen=True)
class SymbolFetch:
    """一个标的在一轮采集里的完整结果，来自同一个来源。"""

    source: str
    bars: dict[str, list[Bar]]
    ticker: Ticker24h


class QuoteAdapter(Protocol):
    name: str

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int, now_ms: int) -> list[Bar]: ...

    async def fetch_ticker(self, symbol: str) -> Ticker24h: ...

    async def aclose(self) -> None: ...


class _HttpAdapter:
    """公共部分：一个 httpx 客户端、统一 symbol 转换和错误包装。"""

    name = ""
    symbol_separator = "/"

    def __init__(
        self,
        base_url: str = "",
        proxy: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        default = DEFAULT_BASE_URLS.get(self.name, "")
        self._base_url = (base_url or default).rstrip("/")
        if not self._base_url:
            raise MarketDataError(f"no base url configured for {self.name}")
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_seconds, connect=timeout_seconds / 2)
        }
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.AsyncClient(**kwargs)

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        await self._client.aclose()

    def native_symbol(self, symbol: str) -> str:
        return symbol.replace("/", self.symbol_separator).upper()

    def interval(self, timeframe: str) -> str:
        if timeframe not in TIMEFRAME_SECONDS:
            raise MarketDataError(f"unsupported timeframe {timeframe}")
        return NATIVE_INTERVALS.get(self.name, {}).get(timeframe, timeframe)

    async def _get(self, path: str, params: dict[str, object]) -> object:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            # httpx 的连接类异常 str() 常常是空的，那样降级原因就只剩一句无用的前缀。
            detail = str(exc).strip() or type(exc).__name__
            raise MarketDataError(f"{self.name} request failed: {detail}") from exc
        if response.status_code >= 400:
            raise MarketDataError(
                f"{self.name} rejected the request: HTTP {response.status_code} {response.text[:160]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError(f"{self.name} response is not JSON") from exc


class BinanceAdapter(_HttpAdapter):
    name = "binance"
    symbol_separator = ""

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int, now_ms: int) -> list[Bar]:
        payload = await self._get(
            "/api/v3/klines",
            {
                "symbol": self.native_symbol(symbol),
                "interval": self.interval(timeframe),
                "limit": max(1, min(limit, 1000)),
            },
        )
        if not isinstance(payload, list):
            raise MarketDataError("binance klines response is not a list")
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
                raise MarketDataError(f"malformed binance kline for {symbol} {timeframe}") from exc
        if not bars:
            raise MarketDataError(f"no binance klines for {symbol} {timeframe}")
        return bars

    async def fetch_ticker(self, symbol: str) -> Ticker24h:
        payload = await self._get("/api/v3/ticker/24hr", {"symbol": self.native_symbol(symbol)})
        if not isinstance(payload, dict):
            raise MarketDataError("binance ticker response is not an object")
        try:
            return Ticker24h(
                last_price=float(payload["lastPrice"]),
                change_pct=float(payload["priceChangePercent"]),
                high=float(payload["highPrice"]),
                low=float(payload["lowPrice"]),
                volume=float(payload["volume"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(f"malformed binance ticker for {symbol}") from exc


class GateAdapter(_HttpAdapter):
    name = "gate"
    symbol_separator = "_"

    # Gate 的 candlesticks 不是常规 OHLCV 顺序，字段固定为：
    # [秒级时间戳, 计价成交量, 收盘, 最高, 最低, 开盘, 基础成交量, 是否收盘]
    _QUOTE_VOLUME = 1
    _CLOSE = 2
    _HIGH = 3
    _LOW = 4
    _OPEN = 5
    _BASE_VOLUME = 6
    _CLOSED = 7

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int, now_ms: int) -> list[Bar]:
        payload = await self._get(
            "/api/v4/spot/candlesticks",
            {
                "currency_pair": self.native_symbol(symbol),
                "interval": self.interval(timeframe),
                "limit": max(1, min(limit, 1000)),
            },
        )
        if not isinstance(payload, list):
            raise MarketDataError("gate candlesticks response is not a list")
        bars: list[Bar] = []
        for row in payload:
            if not isinstance(row, list) or len(row) <= self._CLOSED:
                continue
            try:
                bars.append(
                    Bar(
                        open_time=int(row[0]) * 1000,
                        open=float(row[self._OPEN]),
                        high=float(row[self._HIGH]),
                        low=float(row[self._LOW]),
                        close=float(row[self._CLOSE]),
                        volume=float(row[self._BASE_VOLUME]),
                        closed=str(row[self._CLOSED]).lower() == "true",
                    )
                )
            except (TypeError, ValueError) as exc:
                raise MarketDataError(f"malformed gate candle for {symbol} {timeframe}") from exc
        if not bars:
            raise MarketDataError(f"no gate candles for {symbol} {timeframe}")
        # Gate 文档说返回倒序，实测是正序；两种都兜住。
        bars.sort(key=lambda bar: bar.open_time)
        return bars

    async def fetch_ticker(self, symbol: str) -> Ticker24h:
        payload = await self._get(
            "/api/v4/spot/tickers", {"currency_pair": self.native_symbol(symbol)}
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise MarketDataError("gate ticker response is not a list of objects")
        row = payload[0]
        try:
            return Ticker24h(
                last_price=float(row["last"]),
                change_pct=float(row["change_percentage"]),
                high=float(row["high_24h"]),
                low=float(row["low_24h"]),
                volume=float(row["base_volume"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError(f"malformed gate ticker for {symbol}") from exc


ADAPTERS: dict[str, type[_HttpAdapter]] = {
    "binance": BinanceAdapter,
    "gate": GateAdapter,
}

AdapterFactory = Callable[[MarketSource, str, float], QuoteAdapter]


def build_adapter(source: MarketSource, proxy: str, timeout_seconds: float) -> QuoteAdapter:
    adapter_class = ADAPTERS.get(source.exchange)
    if adapter_class is None:
        raise MarketDataError(f"no adapter for exchange {source.exchange}")
    return adapter_class(
        base_url=source.base_url, proxy=proxy, timeout_seconds=timeout_seconds
    )


class MarketFeed:
    """按顺序尝试多个来源，粘住第一个可用的。

    粘性很重要：不同交易所同一根 K 线会有微小差异，每轮随机换源会让指标抖动。
    """

    def __init__(
        self,
        sources: tuple[MarketSource, ...],
        *,
        proxy: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        if not sources:
            raise MarketDataError("market feed requires at least one source")
        self._sources = tuple(sources)
        self._proxy = proxy
        self._timeout = timeout_seconds
        self._factory = adapter_factory or build_adapter
        self._adapters: dict[str, QuoteAdapter] = {}
        self._active = 0
        self._last_ok: dict[str, float] = {}
        self._last_error: dict[str, str] = {}

    @property
    def sources(self) -> tuple[MarketSource, ...]:
        return self._sources

    @property
    def active_source(self) -> str:
        return self._sources[self._active].exchange

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "exchange": source.exchange,
                "base_url": source.base_url or DEFAULT_BASE_URLS.get(source.exchange, ""),
                "active": index == self._active,
                "last_ok_at": self._last_ok.get(source.exchange),
                "last_error": self._last_error.get(source.exchange, ""),
            }
            for index, source in enumerate(self._sources)
        ]

    def _adapter(self, source: MarketSource) -> QuoteAdapter:
        adapter = self._adapters.get(source.exchange)
        if adapter is None:
            adapter = self._factory(source, self._proxy, self._timeout)
            self._adapters[source.exchange] = adapter
        return adapter

    async def fetch_symbol(
        self,
        symbol: str,
        timeframes: tuple[str, ...],
        limit_for: Callable[[str], int],
        now_ms: int,
    ) -> SymbolFetch:
        """从当前源开始按顺序尝试；一个源只要有一个周期失败就换下一个源。"""
        order = list(range(self._active, len(self._sources))) + list(range(0, self._active))
        errors: list[str] = []
        for index in order:
            source = self._sources[index]
            try:
                adapter = self._adapter(source)
                bars: dict[str, list[Bar]] = {}
                for timeframe in timeframes:
                    bars[timeframe] = await adapter.fetch_klines(
                        symbol, timeframe, limit_for(timeframe), now_ms
                    )
                ticker = await adapter.fetch_ticker(symbol)
            except Exception as exc:  # noqa: BLE001 - 一个源不可用就该换下一个
                # httpx 的连接类异常 str() 可能是空的，那样状态里只剩一句废话。
                detail = str(exc).strip() or type(exc).__name__
                errors.append(f"{source.exchange}: {detail}")
                self._last_error[source.exchange] = detail
                continue
            self._active = index
            self._last_ok[source.exchange] = now_ms
            self._last_error.pop(source.exchange, None)
            return SymbolFetch(source=source.exchange, bars=bars, ticker=ticker)
        raise MarketDataError("all market sources failed: " + " | ".join(errors))

    async def aclose(self) -> None:
        for adapter in self._adapters.values():
            await adapter.aclose()
        self._adapters.clear()
