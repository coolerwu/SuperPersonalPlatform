"""行情工作台的框架无关领域对象与配置规则。

「快层」指 1h 以下的周期：这些判断必须由平台代码秒级产出，不经过模型。
Agent 只覆盖慢层周期，并只能通过快照读取这里产出的结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# 1h 以下的周期由平台代码计算，Agent 不参与；慢层周期才附带 Agent 解读。
FAST_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m")
DEFAULT_TIMEFRAMES: tuple[str, ...] = (*FAST_TIMEFRAMES, "1h", "4h", "1d")
DEFAULT_COMMENTARY_TIMEFRAMES: tuple[str, ...] = ("1h", "4h", "1d")

SEVERITIES: tuple[str, ...] = ("low", "medium", "high")

SUPPORTED_EXCHANGES: tuple[str, ...] = ("binance",)


class MarketConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WatchSymbol:
    """一个自选标的。symbol 用统一写法 `BASE/QUOTE`，交易所由配置决定。"""

    symbol: str
    alias: str = ""

    def __post_init__(self) -> None:
        normalized = normalize_symbol(self.symbol)
        if not normalized:
            raise MarketConfigError("market.symbols[].symbol must look like BASE/QUOTE")
        object.__setattr__(self, "symbol", normalized)
        object.__setattr__(self, "alias", str(self.alias or "").strip())

    @property
    def base(self) -> str:
        return self.symbol.split("/", 1)[0]

    @property
    def quote(self) -> str:
        return self.symbol.split("/", 1)[1]

    @property
    def native(self) -> str:
        """交易所原生交易对写法，例如 BTCUSDT。"""
        return f"{self.base}{self.quote}"

    @property
    def display(self) -> str:
        return f"{self.symbol} {self.alias}".strip()


def normalize_symbol(value: Any) -> str:
    """把 `btc/usdt`、`BTC-USDT`、`BTCUSDT` 统一成 `BTC/USDT`，无法解析时返回空串。"""
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    for separator in ("/", "-", "_"):
        if separator in raw:
            base, _, quote = raw.partition(separator)
            break
    else:
        return ""
    base = base.strip()
    quote = quote.strip()
    if not base or not quote or not base.isalnum() or not quote.isalnum():
        return ""
    return f"{base}/{quote}"


def is_fast_timeframe(timeframe: str) -> bool:
    return timeframe in FAST_TIMEFRAMES


@dataclass(frozen=True)
class MarketSignalsConfig:
    """信号规则阈值。全部为相对量，跨标的和周期可比。"""

    enabled: bool = True
    volume_spike_ratio: float = 1.8
    atr_high_ratio: float = 1.5
    atr_low_ratio: float = 0.75
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    breakout_lookback: int = 20
    cooldown_seconds: int = 900
    notify_min_severity: str = "high"

    def __post_init__(self) -> None:
        if self.volume_spike_ratio <= 1:
            raise MarketConfigError("market.signals.volume_spike_ratio must be greater than 1")
        if self.atr_high_ratio <= 1:
            raise MarketConfigError("market.signals.atr_high_ratio must be greater than 1")
        if not 0 < self.atr_low_ratio < 1:
            raise MarketConfigError("market.signals.atr_low_ratio must be between 0 and 1")
        if not 0 < self.rsi_oversold < self.rsi_overbought < 100:
            raise MarketConfigError("market.signals RSI thresholds must satisfy 0 < oversold < overbought < 100")
        if self.breakout_lookback < 5:
            raise MarketConfigError("market.signals.breakout_lookback must be at least 5")
        if self.cooldown_seconds < 0:
            raise MarketConfigError("market.signals.cooldown_seconds must not be negative")
        if self.notify_min_severity not in SEVERITIES:
            raise MarketConfigError("market.signals.notify_min_severity must be one of low/medium/high")


@dataclass(frozen=True)
class TelegramNotifyConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    min_severity: str = "high"

    def __post_init__(self) -> None:
        if self.min_severity not in SEVERITIES:
            raise MarketConfigError("market.telegram.min_severity must be one of low/medium/high")
        if self.enabled and (not self.bot_token or not self.chat_id):
            raise MarketConfigError("market.telegram requires bot_token and chat_id when enabled")


@dataclass(frozen=True)
class MarketCommentaryConfig:
    """Agent 慢层配置。Agent 只分析 slow 周期，产出异步贴回页面。"""

    enabled: bool = True
    agent_id: str = ""
    interval_seconds: int = 3600
    timeframes: tuple[str, ...] = DEFAULT_COMMENTARY_TIMEFRAMES

    def __post_init__(self) -> None:
        if self.interval_seconds < 300:
            raise MarketConfigError("market.commentary.interval_seconds must be at least 300")
        for timeframe in self.timeframes:
            if timeframe not in TIMEFRAME_SECONDS or is_fast_timeframe(timeframe):
                raise MarketConfigError(
                    "market.commentary.timeframes only accepts 1h and slower timeframes"
                )


@dataclass(frozen=True)
class MarketConfig:
    enabled: bool = False
    exchange: str = "binance"
    # 留空用交易所默认域名；可指向镜像域名或内网网关。
    base_url: str = ""
    proxy: str = ""
    poll_seconds: int = 5
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    history_limit: int = 400
    retention_days: int = 30
    symbols: tuple[WatchSymbol, ...] = ()
    signals: MarketSignalsConfig = field(default_factory=MarketSignalsConfig)
    telegram: TelegramNotifyConfig = field(default_factory=TelegramNotifyConfig)
    commentary: MarketCommentaryConfig = field(default_factory=MarketCommentaryConfig)

    def __post_init__(self) -> None:
        if self.exchange not in SUPPORTED_EXCHANGES:
            raise MarketConfigError(
                f"market.exchange must be one of {', '.join(SUPPORTED_EXCHANGES)}"
            )
        if self.poll_seconds < 5:
            raise MarketConfigError("market.poll_seconds must be at least 5")
        if self.history_limit < 60:
            raise MarketConfigError("market.history_limit must be at least 60")
        if self.retention_days < 1:
            raise MarketConfigError("market.retention_days must be at least 1")
        if not self.timeframes:
            raise MarketConfigError("market.timeframes must not be empty")
        for timeframe in self.timeframes:
            if timeframe not in TIMEFRAME_SECONDS:
                raise MarketConfigError(
                    f"market.timeframes[] must be one of {', '.join(TIMEFRAME_SECONDS)}"
                )
        seen: set[str] = set()
        for entry in self.symbols:
            if entry.symbol in seen:
                raise MarketConfigError(f"market.symbols[] has duplicate symbol {entry.symbol}")
            seen.add(entry.symbol)

    def sorted_timeframes(self) -> tuple[str, ...]:
        """按周期从短到长排列，与矩阵自上而下的顺序一致。"""
        return tuple(sorted(self.timeframes, key=lambda item: TIMEFRAME_SECONDS[item]))

    def find_symbol(self, symbol: str) -> WatchSymbol | None:
        normalized = normalize_symbol(symbol)
        if not normalized:
            return None
        for entry in self.symbols:
            if entry.symbol == normalized:
                return entry
        return None


@dataclass(frozen=True)
class Bar:
    """一根 K 线。open_time 为毫秒时间戳，与交易所一致。"""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True


@dataclass(frozen=True)
class Indicators:
    ema20: float | None = None
    ema60: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr14: float | None = None
    atr_ratio: float | None = None
    bollinger_upper: float | None = None
    bollinger_middle: float | None = None
    bollinger_lower: float | None = None
    volume_ratio: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None


@dataclass(frozen=True)
class TimeframeSnapshot:
    timeframe: str
    fast: bool
    last_bar_time: int
    last_close: float
    change_pct: float
    closed: bool
    seconds_to_close: int
    trend: str
    momentum: str
    volatility: str
    volume_level: str
    signals: tuple[str, ...]
    closes: tuple[float, ...]
    indicators: Indicators

    @property
    def model_dump(self) -> dict[str, Any]:
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "fast": self.fast,
            "last_bar_time": self.last_bar_time,
            "last_close": self.last_close,
            "change_pct": self.change_pct,
            "closed": self.closed,
            "seconds_to_close": self.seconds_to_close,
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "volume_level": self.volume_level,
            "signals": list(self.signals),
            "closes": list(self.closes),
            "indicators": {
                key: getattr(self.indicators, key)
                for key in self.indicators.__dataclass_fields__
            },
        }


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    alias: str
    exchange: str
    last_price: float
    change_pct_24h: float
    high_24h: float
    low_24h: float
    volume_24h: float
    updated_at: str
    data_age_seconds: int
    stale: bool
    timeframes: tuple[TimeframeSnapshot, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "alias": self.alias,
            "exchange": self.exchange,
            "last_price": self.last_price,
            "change_pct_24h": self.change_pct_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "volume_24h": self.volume_24h,
            "updated_at": self.updated_at,
            "data_age_seconds": self.data_age_seconds,
            "stale": self.stale,
            "timeframes": [item.to_dict() for item in self.timeframes],
        }


@dataclass(frozen=True)
class MarketEvent:
    seq: int
    at: str
    symbol: str
    timeframe: str
    rule_id: str
    severity: str
    title: str
    detail: str
    price: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "price": self.price,
        }
