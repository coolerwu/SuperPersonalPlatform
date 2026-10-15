"""行情快层的口径与行为。

重点是保证「同一份 bar 数据得到同一份指标与信号」，以及快层完全不依赖模型。
"""

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.market_service import MarketService, build_analysis_prompt
from server.app.run_service import RunService
from server.app.session_service import SessionService
from server.domain.market import (
    Bar,
    MarketConfig,
    MarketConfigError,
    MarketSignalsConfig,
    TIMEFRAME_SECONDS,
    WatchSymbol,
    normalize_symbol,
)
from server.domain.market_indicators import (
    atr,
    bollinger,
    classify_trend,
    classify_volatility,
    classify_volume,
    compute_indicators,
    detect_signals,
    downgrade_severity,
    ema,
    ema_cross,
    macd,
    rsi,
    seconds_to_close,
    sma,
    volume_ratio,
)
from server.infrastructure.config import parse_market_config, parse_settings
from server.infrastructure.fastapi_app import create_app
from server.infrastructure.market_client import Ticker24h
from server.infrastructure.market_store import MarketStore

NOW_MS = 1_700_000_000_000


# --------------------------------------------------------------------- 指标数学


def test_sma_and_ema_seed_on_the_first_full_window() -> None:
    values = [float(index) for index in range(10)]
    assert sma(values, 3) == [None, None, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    ema_values = ema(values, 3)
    assert ema_values[:2] == [None, None]
    assert ema_values[2] == pytest.approx(1.0)
    # alpha = 2/(3+1) = 0.5
    assert ema_values[3] == pytest.approx(1.0 + 0.5 * (3.0 - 1.0))


def test_series_shorter_than_the_period_returns_all_none() -> None:
    assert sma([1.0, 2.0], 5) == [None, None]
    assert ema([1.0, 2.0], 5) == [None, None]
    assert rsi([1.0, 2.0], 14) == [None, None]


def test_rsi_is_100_for_a_monotonic_rise_and_50_for_a_flat_series() -> None:
    rising = [100.0 + index for index in range(40)]
    assert rsi(rising, 14)[-1] == pytest.approx(100.0)
    flat = [100.0] * 40
    assert rsi(flat, 14)[-1] == pytest.approx(50.0)


def test_macd_of_a_linear_ramp_matches_the_analytical_lag_difference() -> None:
    slope = 0.5
    values = [100.0 + index * slope for index in range(200)]
    macd_series, _, _ = macd(values)
    # 线性序列的 EMA 滞后 (period-1)/2 根，所以 macd = slope * (12.5 - 5.5)
    assert macd_series[-1] == pytest.approx(slope * (12.5 - 5.5), abs=1e-9)


def test_true_range_and_atr_on_a_constant_range_series() -> None:
    bars = [
        Bar(open_time=index * 60_000, open=100, high=102, low=100, close=101, volume=1)
        for index in range(40)
    ]
    series = atr(bars, 14)
    assert series[-1] == pytest.approx(2.0)


def test_bollinger_band_sits_symmetric_around_the_mean() -> None:
    values = [100.0, 102.0] * 20
    upper, middle, lower = bollinger(values, 20, 2.0)
    assert middle[-1] == pytest.approx(101.0)
    assert upper[-1] - middle[-1] == pytest.approx(middle[-1] - lower[-1])
    assert upper[-1] > middle[-1]


def test_swing_levels_exclude_the_current_bar() -> None:
    bars = [
        Bar(open_time=index * 60_000, open=10, high=10 + index, low=9, close=10, volume=1)
        for index in range(25)
    ]
    indicators = compute_indicators(bars)
    # 最后一根 bar 的 high 是 34，但它不能参与前高，否则「收盘高于前高」永远不成立。
    assert indicators.swing_high == pytest.approx(33.0)
    assert indicators.swing_low == pytest.approx(9.0)


def test_volume_ratio_compares_against_the_prior_bars_only() -> None:
    bars = [
        Bar(open_time=index * 60_000, open=1, high=1, low=1, close=1, volume=10)
        for index in range(21)
    ]
    bars.append(Bar(open_time=21 * 60_000, open=1, high=1, low=1, close=1, volume=30))
    assert volume_ratio(bars) == pytest.approx(3.0)


def test_atr_ratio_is_none_when_volatility_has_no_baseline() -> None:
    flat = [
        Bar(open_time=index * 60_000, open=1, high=1, low=1, close=1, volume=1)
        for index in range(40)
    ]
    indicators = compute_indicators(flat, volume_ratio(flat))
    assert indicators.atr14 == 0.0
    assert indicators.atr_ratio is None
    assert classify_volatility(indicators, MarketSignalsConfig()) == "中"


# --------------------------------------------------------------------- 分类与信号


def _bars_from_closes(closes: list[float], volume: float = 10.0, step_ms: int = 60_000) -> list[Bar]:
    return [
        Bar(open_time=index * step_ms, open=close, high=close + 1, low=close - 1, close=close, volume=volume)
        for index, close in enumerate(closes)
    ]


def _ramp_bars(count: int = 250, slope: float = 0.5, volume: float = 10.0, step_ms: int = 60_000) -> list[Bar]:
    return _bars_from_closes([100.0 + index * slope for index in range(count)], volume, step_ms)


def test_trend_follows_ema_alignment() -> None:
    bars = _ramp_bars()
    indicators = compute_indicators(bars, volume_ratio(bars))
    assert classify_trend(bars[-1], indicators) == "up"
    falling = _ramp_bars(slope=-0.5)
    assert classify_trend(falling[-1], compute_indicators(falling)) == "down"


def test_volume_level_buckets() -> None:
    config = MarketSignalsConfig(volume_spike_ratio=1.8)
    bars = _ramp_bars(count=25, volume=10.0)
    assert classify_volume(compute_indicators(bars, 2.0), config) == "放量"
    assert classify_volume(compute_indicators(bars, 1.0), config) == "正常"
    assert classify_volume(compute_indicators(bars, 0.4), config) == "缩量"
    assert classify_volume(compute_indicators(bars, None), config) == "正常"


def test_breakout_fires_only_above_the_prior_high() -> None:
    config = MarketSignalsConfig(breakout_lookback=20)
    # 平稳上涨时每根收盘都低于前一根的最高价，所以不该有突破。
    quiet = _ramp_bars(count=60)
    quiet_rules = {
        signal.rule_id
        for signal in detect_signals(quiet, compute_indicators(quiet, volume_ratio(quiet), 20), config, "1h")
    }
    assert "breakout_high" not in quiet_rules

    prior_high = max(bar.high for bar in quiet[:-1][-20:])
    breaking = quiet[:-1] + [Bar(**{**quiet[-1].__dict__, "close": prior_high + 5})]
    rows = {signal.rule_id: signal for signal in detect_signals(breaking, compute_indicators(breaking, volume_ratio(breaking), 20), config, "1h")}
    assert "breakout_high" in rows
    assert rows["breakout_high"].severity == "high"

    prior_low = min(bar.low for bar in quiet[:-1][-20:])
    caving = quiet[:-1] + [Bar(**{**quiet[-1].__dict__, "close": prior_low - 5})]
    assert "breakdown_low" in {
        signal.rule_id
        for signal in detect_signals(caving, compute_indicators(caving, volume_ratio(caving), 20), config, "1h")
    }


def test_fast_timeframes_downgrade_severity_by_one_level() -> None:
    assert downgrade_severity("high", "1m") == "medium"
    assert downgrade_severity("medium", "5m") == "low"
    assert downgrade_severity("low", "30m") == "low"
    assert downgrade_severity("high", "1h") == "high"
    assert downgrade_severity("medium", "1d") == "medium"


def test_signals_are_disabled_by_config_and_need_enough_bars() -> None:
    bars = _ramp_bars(count=60)
    indicators = compute_indicators(bars, volume_ratio(bars))
    assert detect_signals(bars, indicators, MarketSignalsConfig(enabled=False), "1h") == ()
    assert detect_signals(bars[:3], compute_indicators(bars[:3]), MarketSignalsConfig(), "1h") == ()


def test_ema_cross_reports_the_transition_between_the_last_two_bars_only() -> None:
    # 先跌 150 根再涨 150 根，交叉只应发生在切换过后的某一个确切位置。
    v_shape = _bars_from_closes(
        [100.0 - index * 0.5 for index in range(150)] + [25.0 + index * 1.0 for index in range(150)]
    )
    upward = [index for index in range(61, len(v_shape) + 1) if ema_cross(v_shape[:index]) == "up"]
    assert len(upward) == 1

    inverted = _bars_from_closes(
        [25.0 + index * 1.0 for index in range(150)] + [174.0 - index * 0.5 for index in range(150)]
    )
    downward = [index for index in range(61, len(inverted) + 1) if ema_cross(inverted[:index]) == "down"]
    assert len(downward) == 1

    assert ema_cross(_ramp_bars(count=150)) == ""
    assert ema_cross(_ramp_bars(count=30)) == ""


def test_seconds_to_close_counts_down_within_the_current_bar() -> None:
    open_time = 1_700_000_000_000
    assert seconds_to_close(open_time, "5m", open_time + 30_000) == 270
    assert seconds_to_close(open_time, "5m", open_time) == 300


# --------------------------------------------------------------------- 配置


def test_symbol_normalization_accepts_common_notations() -> None:
    assert normalize_symbol("btc/usdt") == "BTC/USDT"
    assert normalize_symbol("BTC-USDT") == "BTC/USDT"
    assert normalize_symbol("BTC_USDT") == "BTC/USDT"
    assert normalize_symbol("BTCUSDT") == ""
    assert normalize_symbol("") == ""


def test_watch_symbol_exposes_base_quote_and_native_pair() -> None:
    entry = WatchSymbol(symbol="btc/usdt", alias=" 比特币 ")
    assert entry.base == "BTC"
    assert entry.quote == "USDT"
    assert entry.native == "BTCUSDT"
    assert entry.alias == "比特币"


def test_market_config_rejects_unsupported_values() -> None:
    with pytest.raises(MarketConfigError):
        MarketConfig(exchange="nasdaq")
    with pytest.raises(MarketConfigError):
        MarketConfig(poll_seconds=1)
    with pytest.raises(MarketConfigError):
        MarketConfig(timeframes=("3m",))
    with pytest.raises(MarketConfigError):
        MarketConfig(symbols=(WatchSymbol(symbol="BTC/USDT"), WatchSymbol(symbol="BTC/USDT")))
    with pytest.raises(MarketConfigError):
        MarketSignalsConfig(volume_spike_ratio=0.5)
    with pytest.raises(MarketConfigError):
        MarketSignalsConfig(rsi_oversold=80, rsi_overbought=70)


def test_market_config_defaults_are_disabled_and_cover_seven_timeframes() -> None:
    config = MarketConfig()
    assert config.enabled is False
    assert config.sorted_timeframes() == ("1m", "5m", "15m", "30m", "1h", "4h", "1d")
    assert config.find_symbol("btc/usdt") is None


def test_parse_market_config_reads_symbols_and_nested_sections() -> None:
    config = parse_market_config(
        {
            "enabled": True,
            "exchange": "BINANCE",
            "proxy": "http://127.0.0.1:7890",
            "poll_seconds": 5,
            "symbols": ["BTC/USDT", {"symbol": "eth/usdt", "alias": "以太坊"}],
            "signals": {"volume_spike_ratio": 2.0, "notify_min_severity": "medium"},
            "telegram": {"enabled": False},
            "commentary": {"agent_id": "trader", "timeframes": ["1h", "1d"]},
        }
    )
    assert config.enabled is True
    assert config.exchange == "binance"
    assert [entry.symbol for entry in config.symbols] == ["BTC/USDT", "ETH/USDT"]
    assert config.symbols[1].alias == "以太坊"
    assert config.signals.volume_spike_ratio == 2.0
    assert config.signals.notify_min_severity == "medium"
    assert config.commentary.agent_id == "trader"
    assert config.commentary.timeframes == ("1h", "1d")


def test_parse_market_config_refuses_fast_timeframes_in_commentary() -> None:
    with pytest.raises(MarketConfigError):
        parse_market_config({"commentary": {"timeframes": ["5m"]}})


def test_parse_market_config_refuses_malformed_shapes() -> None:
    with pytest.raises(MarketConfigError):
        parse_market_config("nope")
    with pytest.raises(MarketConfigError):
        parse_market_config({"symbols": "BTC/USDT"})
    with pytest.raises(MarketConfigError):
        parse_market_config({"symbols": [42]})


def test_settings_without_a_market_section_stay_disabled() -> None:
    settings = parse_settings({"auth": {"token": "t"}})
    assert settings.market.enabled is False


# --------------------------------------------------------------------- 存储


def test_store_round_trips_bars_and_overwrites_the_live_bar(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        bars = _ramp_bars(count=5)
        store.upsert_bars("binance", "BTC/USDT", "1m", bars)
        loaded = store.load_bars("binance", "BTC/USDT", "1m", 10)
        assert [bar.close for bar in loaded] == [bar.close for bar in bars]

        live = Bar(**{**bars[-1].__dict__, "close": 999.0, "closed": False})
        store.upsert_bars("binance", "BTC/USDT", "1m", [live])
        refreshed = store.load_bars("binance", "BTC/USDT", "1m", 10)
        assert len(refreshed) == 5
        assert refreshed[-1].close == 999.0
        assert refreshed[-1].closed is False
        assert store.latest_bar_time("binance", "BTC/USDT", "1m") == bars[-1].open_time
    finally:
        store.close()


def test_store_limits_and_orders_bars_ascending(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        store.upsert_bars("binance", "BTC/USDT", "1m", _ramp_bars(count=30))
        loaded = store.load_bars("binance", "BTC/USDT", "1m", 10)
        assert len(loaded) == 10
        assert loaded == sorted(loaded, key=lambda bar: bar.open_time)
        assert loaded[-1].open_time == 29 * 60_000
    finally:
        store.close()


def test_store_cooldown_suppresses_repeats_until_it_expires(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        assert store.on_cooldown("BTC/USDT", "5m", "breakout_high", NOW_MS, 900_000) is False
        store.mark_cooldown("BTC/USDT", "5m", "breakout_high", NOW_MS)
        assert store.on_cooldown("BTC/USDT", "5m", "breakout_high", NOW_MS + 1000, 900_000) is True
        assert store.on_cooldown("BTC/USDT", "5m", "breakout_high", NOW_MS + 900_000, 900_000) is False
        # 冷却按 symbol + timeframe + rule 隔离
        assert store.on_cooldown("BTC/USDT", "15m", "breakout_high", NOW_MS + 1000, 900_000) is False
        assert store.on_cooldown("BTC/USDT", "5m", "volume_spike", NOW_MS + 1000, 900_000) is False
        assert store.on_cooldown("BTC/USDT", "5m", "breakout_high", NOW_MS + 1000, 0) is False
    finally:
        store.close()


def test_store_events_get_increasing_seq_and_support_filters(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        first = store.insert_event(
            symbol="BTC/USDT", timeframe="5m", rule_id="breakout_high",
            severity="medium", title="突破", detail="d", price=1.0, at_ms=NOW_MS,
        )
        second = store.insert_event(
            symbol="ETH/USDT", timeframe="1h", rule_id="volume_spike",
            severity="high", title="放量", detail="d", price=2.0, at_ms=NOW_MS,
        )
        assert second.seq > first.seq
        assert store.last_event_seq() == second.seq
        assert [item.symbol for item in store.list_events(after_seq=0)] == ["ETH/USDT", "BTC/USDT"]
        assert [item.symbol for item in store.list_events(after_seq=first.seq)] == ["ETH/USDT"]
        assert [item.symbol for item in store.list_events(symbol="BTC/USDT")] == ["BTC/USDT"]
    finally:
        store.close()


def test_store_read_cursor_tracks_unread_events(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        assert store.last_read_seq() == 0
        store.insert_event(
            symbol="BTC/USDT", timeframe="5m", rule_id="r", severity="low",
            title="t", detail="", price=1.0, at_ms=NOW_MS,
        )
        assert store.last_event_seq() == 1
        assert store.mark_all_read() == 1
        assert store.last_read_seq() == 1
    finally:
        store.close()


def test_store_watchlist_round_trip(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        assert store.watchlist_is_empty() is True
        store.add_watch("BTC/USDT", "比特币", "2026-01-01T00:00:00")
        store.add_watch("ETH/USDT", "", "2026-01-01T00:00:01")
        assert store.watchlist_is_empty() is False
        assert store.list_watchlist() == [("BTC/USDT", "比特币"), ("ETH/USDT", "")]
        store.add_watch("BTC/USDT", "大饼", "2026-01-01T00:00:00")
        assert store.list_watchlist()[0] == ("BTC/USDT", "大饼")
        assert store.remove_watch("BTC/USDT") is True
        assert store.remove_watch("BTC/USDT") is False
    finally:
        store.close()


def test_store_prune_drops_only_data_older_than_the_retention_window(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        old_ms = NOW_MS - 40 * 86400 * 1000
        store.upsert_bars(
            "binance", "BTC/USDT", "1m",
            [Bar(open_time=old_ms, open=1, high=1, low=1, close=1, volume=1)],
        )
        store.upsert_bars(
            "binance", "BTC/USDT", "1m",
            [Bar(open_time=NOW_MS, open=1, high=1, low=1, close=1, volume=1)],
        )
        store.insert_event(
            symbol="BTC/USDT", timeframe="1m", rule_id="r", severity="low",
            title="old", detail="", price=1.0, at_ms=old_ms,
        )
        store.insert_event(
            symbol="BTC/USDT", timeframe="1m", rule_id="r", severity="low",
            title="new", detail="", price=1.0, at_ms=NOW_MS,
        )
        removed = store.prune(30, now_ms=NOW_MS)
        assert removed["events"] == 1
        assert removed["bars"] == 1
        assert [item.title for item in store.list_events()] == ["new"]
        assert len(store.load_bars("binance", "BTC/USDT", "1m", 10)) == 1
    finally:
        store.close()


def test_store_prune_never_drops_below_the_bars_indicators_need(tmp_path: Path) -> None:
    store = MarketStore(tmp_path)
    try:
        step = 86_400_000
        bars = [
            Bar(open_time=NOW_MS - (299 - index) * step, open=1, high=1, low=1, close=1, volume=1)
            for index in range(300)
        ]
        store.upsert_bars("binance", "BTC/USDT", "1d", bars)
        # 保留期只有 1 天，但 1d 周期必须留住 EMA200 需要的历史。
        removed = store.prune(1, now_ms=NOW_MS, min_bars_per_series=200)
        assert removed["bars"] == 100
        kept = store.load_bars("binance", "BTC/USDT", "1d", 1000)
        assert len(kept) == 200
        assert kept[-1].open_time == bars[-1].open_time

        # 保留期比所需历史更长时，保留下限不该阻止过期数据被清理。
        store.prune(10_000, now_ms=NOW_MS, min_bars_per_series=200)
        assert len(store.load_bars("binance", "BTC/USDT", "1d", 1000)) == 200

        # 序列本身不足下限时全部保留。
        assert store.prune(1, now_ms=NOW_MS, min_bars_per_series=10_000)["bars"] == 0
        assert len(store.load_bars("binance", "BTC/USDT", "1d", 1000)) == 200
    finally:
        store.close()


# --------------------------------------------------------------------- 服务


class FakeClient:
    """替掉真实交易所客户端：快层的行为不该依赖网络。"""

    def __init__(self, bars: dict[tuple[str, str], list[Bar]], ticker: Ticker24h) -> None:
        self.bars = bars
        self.ticker = ticker
        self.calls: list[tuple[str, str]] = []

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int, now_ms: int) -> list[Bar]:
        self.calls.append((symbol, timeframe))
        return self.bars[(symbol, timeframe)][-limit:]

    async def fetch_ticker(self, symbol: str) -> Ticker24h:
        return self.ticker

    async def aclose(self) -> None:
        return None


def _service(tmp_path: Path, config: MarketConfig) -> MarketService:
    session_service = SessionService(tmp_path)
    service = MarketService(
        workspace=tmp_path,
        config_path=tmp_path / "config.yaml",
        session_service=session_service,
        run_service=RunService(tmp_path, session_service=session_service),
    )
    service._config = config
    return service


def _bars_for(config: MarketConfig, symbol: str = "BTC/USDT") -> dict[tuple[str, str], list[Bar]]:
    """平稳上涨 + 最后一根放量突破，让每个周期都稳定命中 breakout 与 volume_spike。"""
    native = symbol.replace("/", "")
    bars: dict[tuple[str, str], list[Bar]] = {}
    for timeframe in config.timeframes:
        step = TIMEFRAME_SECONDS[timeframe] * 1000
        base = NOW_MS - step * 300
        closes = [100.0 + index * 0.5 for index in range(300)]
        closes[-1] = max(closes[-21:-1]) + 5
        bars[(native, timeframe)] = [
            Bar(
                open_time=base + index * step,
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=40.0 if index == 299 else 10.0,
                closed=base + index * step + step <= NOW_MS,
            )
            for index, close in enumerate(closes)
        ]
    return bars


def _run_cycle(service: MarketService, client: FakeClient) -> None:
    async def fake_ensure(_config: MarketConfig) -> FakeClient:
        return client

    service._ensure_client = fake_ensure  # type: ignore[assignment]
    asyncio.run(service.run_cycle())


def test_snapshot_covers_every_configured_timeframe_and_flags_fast_ones(tmp_path: Path) -> None:
    config = MarketConfig(
        enabled=True,
        symbols=(WatchSymbol(symbol="BTC/USDT", alias="比特币"),),
    )
    service = _service(tmp_path, config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 1.5, 210.0, 190.0, 1234.0))
    try:
        _run_cycle(service, client)
        snapshot = service.snapshot("btc/usdt")
        assert snapshot is not None
        assert snapshot.alias == "比特币"
        assert snapshot.last_price == 200.0
        assert snapshot.change_pct_24h == 1.5
        assert [frame.timeframe for frame in snapshot.timeframes] == list(config.sorted_timeframes())
        assert [frame.fast for frame in snapshot.timeframes] == [True, True, True, True, False, False, False]
        for frame in snapshot.timeframes:
            assert len(frame.closes) == 20
            assert frame.trend == "up"
            assert frame.last_close > 0
    finally:
        service._store.close()


def test_every_timeframe_is_fetched_every_cycle(tmp_path: Path) -> None:
    config = MarketConfig(enabled=True, symbols=(WatchSymbol(symbol="BTC/USDT"),))
    service = _service(tmp_path, config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 0.0, 210.0, 190.0, 1.0))
    try:
        _run_cycle(service, client)
        assert sorted(item[1] for item in client.calls) == sorted(config.timeframes)
    finally:
        service._store.close()


def test_fast_timeframe_signals_land_in_events_with_downgraded_severity(tmp_path: Path) -> None:
    config = MarketConfig(
        enabled=True,
        symbols=(WatchSymbol(symbol="BTC/USDT"),),
        signals=MarketSignalsConfig(cooldown_seconds=900),
    )
    service = _service(tmp_path, config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 0.0, 210.0, 190.0, 1.0))
    try:
        _run_cycle(service, client)
        events = service.list_events(limit=500)
        by_rule = {}
        for event in events:
            by_rule.setdefault(event.rule_id, {})[event.timeframe] = event.severity
        assert set(by_rule) >= {"breakout_high", "volume_spike"}
        # 严重度按周期降级：1h 以下是 medium，1h 及以上保持 high。
        assert by_rule["breakout_high"]["1m"] == "medium"
        assert by_rule["breakout_high"]["30m"] == "medium"
        assert by_rule["breakout_high"]["1h"] == "high"
        assert by_rule["breakout_high"]["1d"] == "high"
        assert by_rule["volume_spike"]["1m"] == "low"
        assert by_rule["volume_spike"]["1h"] == "medium"
    finally:
        service._store.close()


def test_cooldown_stops_the_same_rule_from_repeating_every_cycle(tmp_path: Path) -> None:
    config = MarketConfig(
        enabled=True,
        symbols=(WatchSymbol(symbol="BTC/USDT"),),
        signals=MarketSignalsConfig(cooldown_seconds=900),
    )
    service = _service(tmp_path, config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 0.0, 210.0, 190.0, 1.0))
    try:
        _run_cycle(service, client)
        after_first = len(service.list_events(limit=500))
        _run_cycle(service, client)
        assert len(service.list_events(limit=500)) == after_first
    finally:
        service._store.close()


def test_watchlist_seeds_from_config_once_and_then_follows_the_page(tmp_path: Path) -> None:
    config = MarketConfig(enabled=True, symbols=(WatchSymbol(symbol="BTC/USDT", alias="比特币"),))
    service = _service(tmp_path, config)
    try:
        assert [item.symbol for item in service.watchlist()] == ["BTC/USDT"]
        service.add_symbol("eth/usdt", "以太坊")
        assert [item.symbol for item in service.watchlist()] == ["BTC/USDT", "ETH/USDT"]
        service.remove_symbol("BTC/USDT")
        # 种子只在列表为空时生效一次，删掉后不会被配置重新塞回来
        assert [item.symbol for item in service.watchlist()] == ["ETH/USDT"]
    finally:
        service._store.close()


def test_add_and_remove_symbol_validate_input(tmp_path: Path) -> None:
    service = _service(tmp_path, MarketConfig(enabled=True))
    try:
        with pytest.raises(Exception):
            service.add_symbol("BTCUSDT")
        with pytest.raises(Exception):
            service.remove_symbol("BTC/USDT")
    finally:
        service._store.close()


def test_analyze_requires_a_snapshot_and_a_configured_agent(tmp_path: Path) -> None:
    config = MarketConfig(enabled=True, symbols=(WatchSymbol(symbol="BTC/USDT"),))
    service = _service(tmp_path, config)
    try:
        with pytest.raises(Exception, match="commentary"):
            asyncio.run(service.analyze("BTC/USDT"))
    finally:
        service._store.close()


COMMENTARY_WORKSPACE_CONFIG = """\
auth:
  token: test-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://example.invalid/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: trader
      name: Trader
      model_id: default
"""


def test_commentary_session_is_absent_until_an_analysis_is_dispatched(tmp_path: Path) -> None:
    from server.domain.market import MarketCommentaryConfig

    config = MarketConfig(
        enabled=True,
        symbols=(WatchSymbol(symbol="BTC/USDT"), WatchSymbol(symbol="ETH/USDT")),
        commentary=MarketCommentaryConfig(agent_id="trader"),
    )
    service = _service(tmp_path, config)
    try:
        # 只读路径不能顺手创建会话，否则每刷一次页面就多一个空 session。
        assert service.commentary_session_id("BTC/USDT") == ""
        assert service.list_commentary("BTC/USDT") == []
        assert not (tmp_path / "sessions").exists()
        assert service.active_commentary_run("BTC/USDT") is None
    finally:
        service._store.close()


def test_analyze_uses_a_session_that_reads_back_through_the_same_id(tmp_path: Path) -> None:
    from server.domain.market import MarketCommentaryConfig

    (tmp_path / "config.yaml").write_text(COMMENTARY_WORKSPACE_CONFIG, encoding="utf-8")
    config = MarketConfig(
        enabled=True,
        symbols=(WatchSymbol(symbol="BTC/USDT"),),
        commentary=MarketCommentaryConfig(agent_id="trader"),
    )
    service = _service(tmp_path, config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 0.0, 210.0, 190.0, 1.0))
    try:
        _run_cycle(service, client)
        dispatched = asyncio.run(service.analyze("BTC/USDT"))
        session_id = dispatched["session_id"]

        # session id 必须能被同一入口读回来，否则解读历史永远是空的。
        assert service.commentary_session_id("BTC/USDT") == session_id
        run = service._run_service.get_run(dispatched["run_id"])
        assert run["input"]["session_id"] == session_id

        # 解读会话独立于 Web Chat 的 session，标题也按标的写在会话自己的 state 里。
        assert session_id.startswith("sess_market_")
        assert (tmp_path / "sessions" / session_id).is_dir()
        assert service._session_service.session_summary(session_id)["title"] == "行情解读 · BTC/USDT"
        assert service.commentary_session_id("ETH/USDT") == ""
    finally:
        service._store.close()


def test_analysis_prompt_marks_fast_timeframes_as_background_only() -> None:
    config = MarketConfig(enabled=True, symbols=(WatchSymbol(symbol="BTC/USDT"),))
    service = _service(Path("/tmp"), config)
    client = FakeClient(_bars_for(config), Ticker24h(200.0, 0.0, 210.0, 190.0, 1.0))
    try:
        _run_cycle(service, client)
        prompt = build_analysis_prompt(service.snapshot("BTC/USDT"), time.time())
        assert "快层" in prompt and "慢层" in prompt
        assert "只对 1h 及以上的周期给出判断" in prompt
        assert "不要给出下单指令" in prompt
        assert "1m" in prompt and "4h" in prompt
    finally:
        service._store.close()


def test_prune_if_due_runs_once_per_day(tmp_path: Path) -> None:
    config = MarketConfig(enabled=True, retention_days=1)
    service = _service(tmp_path, config)
    try:
        first = service.prune_if_due()
        assert service.prune_if_due() == {}
        assert first == {"events": 0, "bars": 0, "cooldowns": 0}
    finally:
        service._store.close()


# --------------------------------------------------------------------- API


API_CONFIG = """\
auth:
  token: secret-token
market:
  enabled: true
  commentary:
    agent_id: assistant
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: assistant
      name: Assistant
      model_id: default
"""


def _market_client(tmp_path: Path) -> TestClient:
    (tmp_path / "config.yaml").write_text(API_CONFIG, encoding="utf-8")
    client = TestClient(create_app(workspace=tmp_path))
    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200
    return client


def test_market_api_requires_authentication(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(API_CONFIG, encoding="utf-8")
    with TestClient(create_app(workspace=tmp_path)) as anonymous:
        assert anonymous.get("/api/market/overview").status_code == 401


def test_market_api_survives_a_workspace_outside_the_market_loop(tmp_path: Path) -> None:
    client = _market_client(tmp_path)
    overview = client.get("/api/market/overview")
    assert overview.status_code == 200
    assert overview.json() == {
        "enabled": True,
        "exchange": "binance",
        "symbols": [],
        "events": {"latest_seq": 0, "last_read_seq": 0, "unread": 0},
    }
    assert client.get("/api/market/events").json()["events"] == []
    assert client.get("/api/market/snapshot", params={"symbol": "BTC/USDT"}).status_code == 404


def test_market_api_manages_the_watchlist(tmp_path: Path) -> None:
    client = _market_client(tmp_path)
    assert client.get("/api/market/symbols").json() == {"symbols": []}
    created = client.post("/api/market/symbols", json={"symbol": "btc/usdt", "alias": "比特币"})
    assert created.status_code == 200
    assert created.json() == {"symbol": "BTC/USDT", "alias": "比特币"}
    assert client.get("/api/market/symbols").json()["symbols"] == [{"symbol": "BTC/USDT", "alias": "比特币"}]
    assert client.post("/api/market/symbols", json={"symbol": "BTCUSDT"}).status_code == 400
    assert client.request("DELETE", "/api/market/symbols", params={"symbol": "BTC/USDT"}).status_code == 200
    assert client.request("DELETE", "/api/market/symbols", params={"symbol": "BTC/USDT"}).status_code == 404


def test_market_api_reports_a_missing_snapshot_instead_of_analyzing(tmp_path: Path) -> None:
    client = _market_client(tmp_path)
    client.post("/api/market/symbols", json={"symbol": "BTC/USDT"})
    response = client.post("/api/market/analyze", json={"symbol": "BTC/USDT"})
    assert response.status_code == 400
    assert "snapshot" in response.json()["detail"]
    assert client.get("/api/market/commentary", params={"symbol": "BTC/USDT"}).json()["commentary"] == []


def test_market_api_read_endpoints_do_not_create_a_commentary_session(tmp_path: Path) -> None:
    client = _market_client(tmp_path)
    body = client.get("/api/market/commentary", params={"symbol": "BTC/USDT"}).json()
    assert body["session_id"] == ""
    assert body["active_run"] is None
    # 只读接口不能顺手创建会话，否则每刷一次页面就多一个空 session。
    assert not (tmp_path / "sessions").exists()


def test_default_market_domain_is_the_keyless_public_market_data_host() -> None:
    """生产机（境内、无代理）只有这个域名连得上，改动它等于让快层整体失效。"""
    from server.infrastructure.market_client import DEFAULT_BASE_URL, ExchangeClient

    assert DEFAULT_BASE_URL == "https://data-api.binance.vision"
    assert MarketConfig().base_url == ""

    client = ExchangeClient()
    try:
        assert client._base_url == DEFAULT_BASE_URL
    finally:
        asyncio.run(client.aclose())

    configured = parse_market_config({"base_url": "https://mirror.internal/"})
    assert configured.base_url == "https://mirror.internal/"
    client = ExchangeClient(base_url=configured.base_url)
    try:
        assert client._base_url == "https://mirror.internal"
    finally:
        asyncio.run(client.aclose())
