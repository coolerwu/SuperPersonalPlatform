"""行情指标与信号规则的唯一实现。

页面矩阵、信号事件流和 Agent 的 `market_snapshot` 工具都调用这里，
不允许出现第二套口径：同一份 bar 数据必须得到同一份指标与信号。
纯函数，无框架、无 IO。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from server.domain.market import (
    Bar,
    Indicators,
    MarketSignalsConfig,
    TIMEFRAME_SECONDS,
    is_fast_timeframe,
)

MOMENTUM_STRONG = "强"
MOMENTUM_MEDIUM = "中"
MOMENTUM_WEAK = "弱"

TREND_UP = "up"
TREND_DOWN = "down"
TREND_FLAT = "flat"

VOLATILITY_HIGH = "高"
VOLATILITY_MEDIUM = "中"
VOLATILITY_LOW = "低"

VOLUME_SPIKE = "放量"
VOLUME_NORMAL = "正常"
VOLUME_THIN = "缩量"

_ATR_BASELINE_LOOKBACK = 100
_ATR_BASELINE_MINIMUM = 20


@dataclass(frozen=True)
class DetectedSignal:
    rule_id: str
    severity: str
    title: str
    detail: str


def sma(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    running = sum(values[:period])
    result[period - 1] = running / period
    for index in range(period, len(values)):
        running += values[index] - values[index - period]
        result[index] = running / period
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    """标准 EMA：用前 period 个值的 SMA 做种子，之后按 alpha=2/(period+1) 平滑。"""
    if period < 1:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = 2.0 / (period + 1)
    previous = sum(values[:period]) / period
    result[period - 1] = previous
    for index in range(period, len(values)):
        previous = previous + alpha * (values[index] - previous)
        result[index] = previous
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI。"""
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        delta = values[index] - values[index - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    average_gain = gains / period
    average_loss = losses / period
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        delta = values[index] - values[index - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + strength)


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_series = ema(values, fast)
    slow_series = ema(values, slow)
    macd_series: list[float | None] = [
        None if (fast_value is None or slow_value is None) else fast_value - slow_value
        for fast_value, slow_value in zip(fast_series, slow_series)
    ]
    signal_series = _ema_of_optional(macd_series, signal)
    hist_series: list[float | None] = [
        None if (macd_value is None or signal_value is None) else macd_value - signal_value
        for macd_value, signal_value in zip(macd_series, signal_series)
    ]
    return macd_series, signal_series, hist_series


def _ema_of_optional(values: list[float | None], period: int) -> list[float | None]:
    """对只在头部带空值的序列做 EMA；中间出现空值时放弃（不应发生）。"""
    result: list[float | None] = [None] * len(values)
    first_index = next((index for index, value in enumerate(values) if value is not None), None)
    if first_index is None:
        return result
    dense = values[first_index:]
    if any(value is None for value in dense):
        return result
    seeded = ema([float(value) for value in dense], period)
    for offset, value in enumerate(seeded):
        result[first_index + offset] = value
    return result


def true_ranges(bars: list[Bar]) -> list[float | None]:
    result: list[float | None] = [None] * len(bars)
    for index in range(1, len(bars)):
        bar = bars[index]
        previous_close = bars[index - 1].close
        result[index] = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
    return result


def atr(bars: list[Bar], period: int = 14) -> list[float | None]:
    """Wilder ATR。"""
    ranges = true_ranges(bars)
    result: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return result
    window = [value for value in ranges[1 : period + 1] if value is not None]
    if len(window) < period:
        return result
    previous = sum(window) / period
    result[period] = previous
    for index in range(period + 1, len(bars)):
        value = ranges[index]
        if value is None:
            continue
        previous = (previous * (period - 1) + value) / period
        result[index] = previous
    return result


def bollinger(
    values: list[float],
    period: int = 20,
    multiplier: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index, mean in enumerate(middle):
        if mean is None or index + 1 < period:
            continue
        window = values[index - period + 1 : index + 1]
        variance = sum((value - mean) ** 2 for value in window) / period
        deviation = math.sqrt(variance)
        upper[index] = mean + multiplier * deviation
        lower[index] = mean - multiplier * deviation
    return upper, middle, lower


def compute_indicators(
    bars: list[Bar],
    volume_ratio: float | None = None,
    breakout_lookback: int = 20,
) -> Indicators:
    """计算最后一根 bar 上的全部指标。传入空或过短序列时相关字段为 None。

    swing_high / swing_low 只取当前 bar 之前 breakout_lookback 根，
    否则「收盘价高于前高」永远不成立。
    """
    if not bars:
        return Indicators()
    closes = [bar.close for bar in bars]
    last = len(bars) - 1

    ema20 = ema(closes, 20)
    ema60 = ema(closes, 60)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes, 14)
    macd_series, signal_series, hist_series = macd(closes)
    atr14 = atr(bars, 14)
    upper, middle, lower = bollinger(closes, 20, 2.0)

    prior = bars[:-1][-breakout_lookback:]

    return Indicators(
        ema20=ema20[last],
        ema60=ema60[last],
        ema200=ema200[last],
        rsi14=rsi14[last],
        macd=macd_series[last],
        macd_signal=signal_series[last],
        macd_hist=hist_series[last],
        atr14=atr14[last],
        atr_ratio=atr_ratio(atr14),
        bollinger_upper=upper[last],
        bollinger_middle=middle[last],
        bollinger_lower=lower[last],
        volume_ratio=volume_ratio,
        swing_high=max((bar.high for bar in prior), default=None),
        swing_low=min((bar.low for bar in prior), default=None),
    )


def atr_ratio(series: list[float | None]) -> float | None:
    """当前 ATR 相对自身近期基线的比值，用于跨标的、跨周期比较波动。"""
    values = [value for value in series if value is not None]
    if len(values) < _ATR_BASELINE_MINIMUM:
        return None
    window = values[-_ATR_BASELINE_LOOKBACK:]
    baseline = sum(window) / len(window)
    if baseline <= 0:
        return None
    return values[-1] / baseline


def volume_ratio(bars: list[Bar], lookback: int = 20) -> float | None:
    """当前 bar 成交量相对之前 lookback 根均量的比值（量比）。"""
    if len(bars) < lookback + 1:
        return None
    previous = bars[-lookback - 1 : -1]
    baseline = sum(bar.volume for bar in previous) / len(previous)
    if baseline <= 0:
        return None
    return bars[-1].volume / baseline


def classify_trend(bar: Bar, indicators: Indicators) -> str:
    ema20 = indicators.ema20
    ema60 = indicators.ema60
    if ema20 is None or ema60 is None:
        return TREND_FLAT
    if bar.close > ema20 > ema60:
        return TREND_UP
    if bar.close < ema20 < ema60:
        return TREND_DOWN
    return TREND_FLAT


def classify_momentum(bar: Bar, indicators: Indicators) -> str:
    rsi_value = indicators.rsi14
    if rsi_value is None:
        return MOMENTUM_MEDIUM
    histogram = indicators.macd_hist
    # MACD 柱按价格量级缩放，用相对容差判断正负，避免浮点残差把持平判成反向。
    tolerance = abs(bar.close) * 1e-9
    if rsi_value >= 60 and (histogram is None or histogram >= -tolerance):
        return MOMENTUM_STRONG
    if rsi_value <= 40 and (histogram is None or histogram <= tolerance):
        return MOMENTUM_WEAK
    return MOMENTUM_MEDIUM


def classify_volatility(indicators: Indicators, config: MarketSignalsConfig) -> str:
    ratio = indicators.atr_ratio
    if ratio is None:
        return VOLATILITY_MEDIUM
    if ratio >= config.atr_high_ratio:
        return VOLATILITY_HIGH
    if ratio <= config.atr_low_ratio:
        return VOLATILITY_LOW
    return VOLATILITY_MEDIUM


def classify_volume(indicators: Indicators, config: MarketSignalsConfig) -> str:
    ratio = indicators.volume_ratio
    if ratio is None:
        return VOLUME_NORMAL
    if ratio >= config.volume_spike_ratio:
        return VOLUME_SPIKE
    if ratio <= 1 / config.volume_spike_ratio:
        return VOLUME_THIN
    return VOLUME_NORMAL


def detect_signals(
    bars: list[Bar],
    indicators: Indicators,
    config: MarketSignalsConfig,
    timeframe: str,
) -> tuple[DetectedSignal, ...]:
    """规则命中即产生信号。严重度按周期降级：1m 的突破不该和 1h 的突破同级。"""
    if not config.enabled or len(bars) < 5:
        return ()
    bar = bars[-1]
    found: list[DetectedSignal] = []

    swing_high = indicators.swing_high
    swing_low = indicators.swing_low
    if swing_high is not None and bar.close > swing_high:
        found.append(
            DetectedSignal(
                rule_id="breakout_high",
                severity="high",
                title=f"{timeframe} 突破 {_price(swing_high)}",
                detail=f"收盘 {_price(bar.close)} 高于前 {config.breakout_lookback} 根高点",
            )
        )
    if swing_low is not None and bar.close < swing_low:
        found.append(
            DetectedSignal(
                rule_id="breakdown_low",
                severity="high",
                title=f"{timeframe} 跌破 {_price(swing_low)}",
                detail=f"收盘 {_price(bar.close)} 低于前 {config.breakout_lookback} 根低点",
            )
        )

    ratio = indicators.volume_ratio
    if ratio is not None and ratio >= config.volume_spike_ratio:
        found.append(
            DetectedSignal(
                rule_id="volume_spike",
                severity="medium",
                title=f"{timeframe} 量比 {ratio:.1f}x 放量",
                detail=f"当前成交量是前 20 根均量的 {ratio:.1f} 倍",
            )
        )

    rsi_value = indicators.rsi14
    if rsi_value is not None and rsi_value >= config.rsi_overbought:
        found.append(
            DetectedSignal(
                rule_id="rsi_overbought",
                severity="low",
                title=f"{timeframe} RSI 超买 {rsi_value:.1f}",
                detail=f"RSI(14) 达到 {rsi_value:.1f}，阈值 {config.rsi_overbought:.0f}",
            )
        )
    if rsi_value is not None and rsi_value <= config.rsi_oversold:
        found.append(
            DetectedSignal(
                rule_id="rsi_oversold",
                severity="low",
                title=f"{timeframe} RSI 超卖 {rsi_value:.1f}",
                detail=f"RSI(14) 降到 {rsi_value:.1f}，阈值 {config.rsi_oversold:.0f}",
            )
        )

    cross = ema_cross(bars)
    if cross == TREND_UP:
        found.append(
            DetectedSignal(
                rule_id="ema_cross_golden",
                severity="medium",
                title=f"{timeframe} 均线金叉",
                detail="EMA20 上穿 EMA60",
            )
        )
    elif cross == TREND_DOWN:
        found.append(
            DetectedSignal(
                rule_id="ema_cross_dead",
                severity="medium",
                title=f"{timeframe} 均线死叉",
                detail="EMA20 下穿 EMA60",
            )
        )

    upper = indicators.bollinger_upper
    lower = indicators.bollinger_lower
    if upper is not None and bar.close > upper:
        found.append(
            DetectedSignal(
                rule_id="bollinger_upper_break",
                severity="low",
                title=f"{timeframe} 触及布林上轨",
                detail=f"收盘 {_price(bar.close)} 高于上轨 {_price(upper)}",
            )
        )
    if lower is not None and bar.close < lower:
        found.append(
            DetectedSignal(
                rule_id="bollinger_lower_break",
                severity="low",
                title=f"{timeframe} 触及布林下轨",
                detail=f"收盘 {_price(bar.close)} 低于下轨 {_price(lower)}",
            )
        )

    return tuple(
        DetectedSignal(
            rule_id=signal.rule_id,
            severity=downgrade_severity(signal.severity, timeframe),
            title=signal.title,
            detail=signal.detail,
        )
        for signal in found
    )


def downgrade_severity(severity: str, timeframe: str) -> str:
    """1h 以下的信号降一级：分钟级噪音不应触发和小时级同级的提醒。"""
    if not is_fast_timeframe(timeframe):
        return severity
    if severity == "high":
        return "medium"
    if severity == "medium":
        return "low"
    return severity


def ema_cross(bars: list[Bar]) -> str:
    """最近两根 bar 之间 EMA20 与 EMA60 是否发生交叉。"""
    closes = [bar.close for bar in bars]
    ema20 = ema(closes, 20)
    ema60 = ema(closes, 60)
    if len(bars) < 2 or ema20[-1] is None or ema60[-1] is None or ema20[-2] is None or ema60[-2] is None:
        return ""
    previous_diff = ema20[-2] - ema60[-2]
    current_diff = ema20[-1] - ema60[-1]
    if previous_diff <= 0 < current_diff:
        return TREND_UP
    if previous_diff >= 0 > current_diff:
        return TREND_DOWN
    return ""


def severity_rank(severity: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(severity, 0)


def seconds_to_close(open_time_ms: int, timeframe: str, now_ms: int) -> int:
    span = TIMEFRAME_SECONDS[timeframe] * 1000
    elapsed = now_ms - open_time_ms
    if elapsed < 0:
        return TIMEFRAME_SECONDS[timeframe]
    return max(0, int((span - (elapsed % span)) / 1000))


def _price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")
