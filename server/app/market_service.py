"""行情工作台应用服务：快层采集、指标、信号，以及慢层的 Agent 解读编排。

快层（1m–30m）的每一个数值都由本模块的确定性计算产出，不经过模型；
慢层只是把快照交给 Agent 做解释，异步贴回页面，不阻塞快层刷新。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from server.app.run_service import RunService, SessionRunConflictError
from server.app.session_service import SessionService
from server.domain.market import (
    Bar,
    MarketConfig,
    MarketEvent,
    SPOT_MARKET_KEY,
    SymbolSnapshot,
    TIMEFRAME_SECONDS,
    TimeframeSnapshot,
    WatchSymbol,
    is_fast_timeframe,
    normalize_symbol,
)
from server.domain.market_indicators import (
    DetectedSignal,
    compute_indicators,
    detect_signals,
    seconds_to_close,
    severity_rank,
    volume_ratio,
    classify_momentum,
    classify_trend,
    classify_volatility,
    classify_volume,
)
from server.infrastructure.config import load_settings, resolve_proxy
from server.infrastructure.market_client import MarketDataError, MarketFeed, SymbolFetch, Ticker24h
from server.infrastructure.market_store import MarketStore
from server.infrastructure.telegram_notifier import TelegramNotifier

MARKET_RUN_SOURCE = "market"
COMMENTARY_CHANNEL = "market"
LAST_PRUNE_KEY = "last_prune_day"
# 解读面板只列最近这些条，取多了既没用又会拖慢页面。
COMMENTARY_HISTORY = 20


class MarketServiceError(RuntimeError):
    """行情服务的可恢复业务错误，直接映射成 API 的 4xx。"""


class MarketRunConflictError(MarketServiceError):
    """该标的已经有解读 Run 在跑，映射成 409。"""


class MarketService:
    def __init__(
        self,
        *,
        workspace: Path,
        config_path: Path,
        session_service: SessionService,
        run_service: RunService,
        run_worker_service: Any | None = None,
        system_log_service: Any | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._config_path = Path(config_path)
        self._session_service = session_service
        self._run_service = run_service
        self._run_worker_service = run_worker_service
        self._system_log_service = system_log_service

        self._store = MarketStore(self._workspace)
        self._config: MarketConfig = MarketConfig()
        self._global_proxy = ""
        self._config_error_logged = ""
        self._feed: MarketFeed | None = None
        self._feed_key: tuple[tuple[str, str], ...] = ()
        self._snapshots: dict[str, SymbolSnapshot] = {}
        self._tickers: dict[str, Ticker24h] = {}
        self._notifier: TelegramNotifier | None = None
        self._notifier_key: tuple[str, str, str] = ("", "", "")
        self._cycle_lock = asyncio.Lock()
        # 后台 loop 可能还没跑第一轮，接口就要用配置，所以构造时先读一次。
        self.reload_config()

    # ------------------------------------------------------------------ 生命周期

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            config = self.reload_config()
            interval = max(5, config.poll_seconds) if config.enabled else 30
            try:
                if config.enabled:
                    await self.run_cycle()
                    self.prune_if_due(config)
            except Exception as exc:  # noqa: BLE001 - 采集失败不能拖垮后台 loop
                self._log(f"market cycle failed: {exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def aclose(self) -> None:
        if self._feed is not None:
            await self._feed.aclose()
            self._feed = None
        self._store.close()

    # ------------------------------------------------------------------ 配置

    def reload_config(self) -> MarketConfig:
        """每次读取当前 config.yaml 的 market 段；读失败时保留上一份可用配置。"""
        try:
            settings = load_settings(self._config_path)
        except Exception as exc:  # noqa: BLE001 - 配置文件可能正在被写入
            message = f"market config reload failed: {exc}"
            if message != self._config_error_logged:
                self._log(message)
                self._config_error_logged = message
            return self._config
        self._config_error_logged = ""
        previous = self._config
        self._config = settings.market
        self._global_proxy = settings.network.proxy
        if self._config != previous:
            self._log(
                f"market config updated: enabled={self._config.enabled} "
                f"sources={','.join(self._config.source_names())} "
                f"watchlist={len(self._store.list_watchlist())} "
                f"commentary={self._config.commentary.agent_id or '未配置'}"
            )
        return self._config

    @property
    def config(self) -> MarketConfig:
        return self._config

    def _log(self, message: str) -> None:
        if self._system_log_service is not None:
            try:
                self._system_log_service.append_line(f"[market] {message}")
            except Exception:  # noqa: BLE001 - 日志失败不应影响行情
                pass

    # ------------------------------------------------------------------ 采集

    async def run_cycle(self) -> None:
        async with self._cycle_lock:
            await self._run_cycle_locked()

    async def _run_cycle_locked(self) -> None:
        config = self._config
        if not config.enabled:
            return
        feed = await self._ensure_feed(config)
        now_ms = int(time.time() * 1000)
        for watch in self.watchlist(config):
            try:
                await self._ingest_symbol(feed, config, watch, now_ms)
            except MarketDataError as exc:
                # 单个标的失败不影响其它标的；页面会按快照的数据时间标注延迟。
                self._log(f"market ingest failed for {watch.symbol}: {exc}")
            except Exception as exc:  # noqa: BLE001
                self._log(f"market ingest error for {watch.symbol}: {exc}")

    async def _ingest_symbol(
        self,
        feed: MarketFeed,
        config: MarketConfig,
        watch: WatchSymbol,
        now_ms: int,
    ) -> None:
        timeframes = config.sorted_timeframes()
        fetched: SymbolFetch = await feed.fetch_symbol(
            watch.symbol,
            timeframes,
            lambda timeframe: self._plan_limit(config, watch, timeframe, now_ms),
            now_ms,
        )
        # 只有整个标的从同一个来源取全了才落盘，避免半份数据混进历史。
        for timeframe in timeframes:
            self._store.upsert_bars(
                SPOT_MARKET_KEY, watch.symbol, timeframe, fetched.bars[timeframe]
            )
        self._tickers[watch.symbol] = fetched.ticker

        snapshot, raw_signals = self._build_snapshot(
            config, watch, fetched.ticker, fetched.source, now_ms
        )
        self._snapshots[watch.symbol] = snapshot
        await self._emit_signals(config, watch, raw_signals, now_ms)

    def _plan_limit(self, config: MarketConfig, watch: WatchSymbol, timeframe: str, now_ms: int) -> int:
        """只补回停机期间缺的 bar，避免每轮都拉满历史。"""
        latest = self._store.latest_bar_time(SPOT_MARKET_KEY, watch.symbol, timeframe)
        if latest <= 0:
            return config.history_limit
        span = TIMEFRAME_SECONDS[timeframe] * 1000
        missing = int(max(0, now_ms - latest) // span) + 1
        return max(3, min(config.history_limit, missing + 2))

    async def _ensure_feed(self, config: MarketConfig) -> MarketFeed:
        # market.proxy 为空就跟随全局 network.proxy；写 direct 才强制直连。
        proxy = resolve_proxy(config.proxy, self._global_proxy)
        key = tuple((source.exchange, source.base_url) for source in config.sources) + (
            ("__proxy__", proxy),
        )
        if self._feed is not None and self._feed_key == key:
            return self._feed
        if self._feed is not None:
            await self._feed.aclose()
        self._feed = MarketFeed(config.sources, proxy=proxy)
        self._feed_key = key
        return self._feed

    def feed_status(self) -> list[dict[str, Any]]:
        return self._feed.status() if self._feed is not None else []

    # ------------------------------------------------------------------ 自选标的

    def watchlist(self, config: MarketConfig | None = None) -> list[WatchSymbol]:
        active = config or self._config
        entries = self._store.list_watchlist()
        if not entries and active.symbols:
            # 首次启用时用配置里的种子初始化，之后以页面维护的列表为准。
            for seed in active.symbols:
                self._store.add_watch(seed.symbol, seed.alias, _now_text())
            entries = self._store.list_watchlist()
        return [WatchSymbol(symbol=symbol, alias=alias) for symbol, alias in entries]

    def add_symbol(self, symbol: str, alias: str = "") -> WatchSymbol:
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise MarketServiceError("symbol must look like BTC/USDT")
        entry = WatchSymbol(symbol=normalized, alias=alias)
        self._store.add_watch(entry.symbol, entry.alias, _now_text())
        return entry

    def remove_symbol(self, symbol: str) -> None:
        normalized = normalize_symbol(symbol)
        if not normalized or not self._store.remove_watch(normalized):
            raise MarketServiceError("symbol is not in the watchlist")
        self._snapshots.pop(normalized, None)
        self._tickers.pop(normalized, None)

    # ------------------------------------------------------------------ 快照

    def snapshot(self, symbol: str) -> SymbolSnapshot | None:
        normalized = normalize_symbol(symbol)
        return self._snapshots.get(normalized) if normalized else None

    def load_bars(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict[str, Any]]:
        normalized = normalize_symbol(symbol)
        if not normalized or timeframe not in TIMEFRAME_SECONDS:
            return []
        bars = self._store.load_bars(SPOT_MARKET_KEY, normalized, timeframe, limit)
        return [
            {
                "open_time": bar.open_time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "closed": bar.closed,
            }
            for bar in bars
        ]

    def all_snapshots(self) -> list[SymbolSnapshot]:
        order = [watch.symbol for watch in self.watchlist()]
        ranked = {symbol: index for index, symbol in enumerate(order)}
        return sorted(
            self._snapshots.values(),
            key=lambda item: ranked.get(item.symbol, len(ranked)),
        )

    def _build_snapshot(
        self,
        config: MarketConfig,
        watch: WatchSymbol,
        ticker: Ticker24h,
        source: str,
        now_ms: int,
    ) -> tuple[SymbolSnapshot, list[tuple[str, DetectedSignal]]]:
        frames: list[TimeframeSnapshot] = []
        raw_signals: list[tuple[str, DetectedSignal]] = []
        newest_bar_ms = 0
        for timeframe in config.sorted_timeframes():
            bars = self._store.load_bars(SPOT_MARKET_KEY, watch.symbol, timeframe, config.history_limit)
            if not bars:
                continue
            frame, detected = self._build_timeframe(config, timeframe, bars, now_ms)
            frames.append(frame)
            newest_bar_ms = max(newest_bar_ms, frame.last_bar_time)
            raw_signals.extend((timeframe, signal) for signal in detected)

        age_seconds = max(0, (now_ms - newest_bar_ms) // 1000) if newest_bar_ms else 0
        shortest = TIMEFRAME_SECONDS[config.sorted_timeframes()[0]] if frames else 60
        return (
            SymbolSnapshot(
                symbol=watch.symbol,
                alias=watch.alias,
                source=source,
                last_price=ticker.last_price,
                change_pct_24h=ticker.change_pct,
                high_24h=ticker.high,
                low_24h=ticker.low,
                volume_24h=ticker.volume,
                updated_at=_now_text(),
                data_age_seconds=age_seconds,
                stale=bool(newest_bar_ms) and age_seconds > 3 * shortest,
                timeframes=tuple(frames),
            ),
            raw_signals,
        )

    def _build_timeframe(
        self,
        config: MarketConfig,
        timeframe: str,
        bars: list[Bar],
        now_ms: int,
    ) -> tuple[TimeframeSnapshot, tuple[DetectedSignal, ...]]:
        bar = bars[-1]
        ratio = volume_ratio(bars)
        indicators = compute_indicators(bars, ratio, config.signals.breakout_lookback)
        previous_close = bars[-2].close if len(bars) > 1 else bar.close
        change_pct = ((bar.close - previous_close) / previous_close * 100) if previous_close else 0.0
        detected = detect_signals(bars, indicators, config.signals, timeframe)
        frame = TimeframeSnapshot(
            timeframe=timeframe,
            fast=is_fast_timeframe(timeframe),
            last_bar_time=bar.open_time,
            last_close=bar.close,
            change_pct=change_pct,
            closed=bar.closed,
            seconds_to_close=seconds_to_close(bar.open_time, timeframe, now_ms),
            trend=classify_trend(bar, indicators),
            momentum=classify_momentum(bar, indicators),
            volatility=classify_volatility(indicators, config.signals),
            volume_level=classify_volume(indicators, config.signals),
            signals=tuple(signal.title for signal in detected),
            closes=tuple(item.close for item in bars[-20:]),
            indicators=indicators,
        )
        return frame, detected

    # ------------------------------------------------------------------ 信号事件

    async def _emit_signals(
        self,
        config: MarketConfig,
        watch: WatchSymbol,
        raw_signals: list[tuple[str, DetectedSignal]],
        now_ms: int,
    ) -> None:
        cooldown_ms = config.signals.cooldown_seconds * 1000
        for timeframe, signal in raw_signals:
            if self._store.on_cooldown(watch.symbol, timeframe, signal.rule_id, now_ms, cooldown_ms):
                continue
            self._store.mark_cooldown(watch.symbol, timeframe, signal.rule_id, now_ms)
            event = self._store.insert_event(
                symbol=watch.symbol,
                timeframe=timeframe,
                rule_id=signal.rule_id,
                severity=signal.severity,
                title=signal.title,
                detail=signal.detail,
                price=self._last_price(watch.symbol),
                at_ms=now_ms,
            )
            await self._notify(config, event)

    def _last_price(self, symbol: str) -> float:
        ticker = self._tickers.get(symbol)
        return ticker.last_price if ticker else 0.0

    async def _notify(self, config: MarketConfig, event: MarketEvent) -> None:
        notifier = self._ensure_notifier(config)
        if notifier is None or not notifier.enabled:
            return
        threshold = max(
            severity_rank(config.signals.notify_min_severity),
            severity_rank(config.telegram.min_severity),
        )
        if severity_rank(event.severity) < threshold:
            return
        ok, detail = await notifier.send(_telegram_text(event))
        if not ok:
            self._log(f"telegram notify failed for {event.symbol} {event.timeframe}: {detail}")

    def _ensure_notifier(self, config: MarketConfig) -> TelegramNotifier | None:
        # 和行情采集用同一套解析：market.proxy 为空就跟随全局代理。
        # Telegram 的 API 在境内不通，漏掉这一步会让推送静默地全部失败。
        proxy = resolve_proxy(config.proxy, self._global_proxy)
        key = (config.telegram.bot_token, config.telegram.chat_id, proxy)
        if self._notifier is None or self._notifier_key != key:
            self._notifier = TelegramNotifier(config.telegram, proxy)
            self._notifier_key = key
        return self._notifier

    def list_events(self, *, after_seq: int = 0, symbol: str = "", limit: int = 100) -> list[MarketEvent]:
        normalized = normalize_symbol(symbol) if symbol else ""
        return self._store.list_events(after_seq=after_seq, symbol=normalized or "", limit=limit)

    def events_summary(self) -> dict[str, Any]:
        latest = self._store.last_event_seq()
        last_read = self._store.last_read_seq()
        return {"latest_seq": latest, "last_read_seq": last_read, "unread": max(0, latest - last_read)}

    def mark_events_read(self) -> dict[str, Any]:
        self._store.mark_all_read()
        return self.events_summary()

    # ------------------------------------------------------------------ Agent 慢层

    def _commentary_session_key(self, config: MarketConfig, symbol: str) -> str:
        return f"commentary_session:{SPOT_MARKET_KEY}:{config.commentary.agent_id}:{symbol}"

    def commentary_session_id(self, symbol: str, config: MarketConfig | None = None) -> str:
        """返回这个标的已经创建过的解读会话；没创建过就返回空。

        `SessionService.build_session_id` 算出来的是渠道身份 active key，不是 session id，
        两者并不相同，所以真正的会话 id 在 analyze 创建后记在本服务自己的存储里。
        GET 路径不创建会话，也不该靠猜。
        """
        active = config or self._config
        normalized = normalize_symbol(symbol)
        if not normalized or not active.commentary.agent_id:
            return ""
        session_id = self._store.get_meta(self._commentary_session_key(active, normalized))
        if session_id and self._session_service.exists(session_id):
            return session_id
        return ""

    async def analyze(self, symbol: str, *, source: str = "manual") -> dict[str, Any]:
        config = self._config
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise MarketServiceError("symbol must look like BTC/USDT")
        if not config.commentary.enabled:
            raise MarketServiceError("market commentary is disabled")
        if not config.commentary.agent_id:
            raise MarketServiceError("market.commentary.agent_id is not configured")
        watch = self._find_watch(normalized)
        if watch is None:
            raise MarketServiceError("symbol is not in the watchlist")
        snapshot = self._snapshots.get(normalized)
        if snapshot is None:
            raise MarketServiceError("no market snapshot available yet, wait for the next poll")

        identity = self._session_service.get_or_create(
            channel=COMMENTARY_CHANNEL,
            channel_account_id=SPOT_MARKET_KEY,
            peer_type="symbol",
            peer_id=normalized.replace("/", ""),
            agent_id=config.commentary.agent_id,
            metadata={"symbol": normalized},
        )
        session_id = identity.session_id
        self._store.set_meta(self._commentary_session_key(config, normalized), session_id)
        prompt = build_analysis_prompt(snapshot, time.time())
        try:
            run = await self._run_service.create_run(
                content=prompt,
                agent_id=config.commentary.agent_id,
                source=MARKET_RUN_SOURCE,
                session_id=session_id,
                metadata={
                    "market": {
                        "symbol": normalized,
                        "timeframes": list(config.commentary.timeframes),
                        "trigger": source,
                    }
                },
            )
        except SessionRunConflictError as exc:
            raise MarketRunConflictError(
                "this symbol already has an analysis run in progress"
            ) from exc
        if self._run_worker_service is not None:
            self._run_worker_service.wake()
        # create_run 已追加首条消息并写了自动标题（截断的 prompt），换成可读的手动标题。
        self._session_service.set_title(session_id, f"行情解读 · {normalized}")
        return {
            "run_id": str(run.get("run_id") or ""),
            "session_id": session_id,
            "symbol": normalized,
        }

    def active_commentary_run(self, symbol: str) -> dict[str, Any] | None:
        session_id = self.commentary_session_id(symbol)
        if not session_id:
            return None
        return self._run_service.active_run_for_session(session_id)

    def list_commentary(self, symbol: str, *, limit: int = COMMENTARY_HISTORY) -> list[dict[str, Any]]:
        session_id = self.commentary_session_id(symbol)
        if not session_id or not self._session_service.exists(session_id):
            return []
        messages = self._session_service.read_messages(session_id, limit=limit * 2)
        entries: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("role") or "") != "assistant":
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            entries.append(
                {
                    "at": str(message.get("created_at") or ""),
                    "run_id": str(message.get("run_id") or ""),
                    "content": content,
                    "confidence": parse_confidence(content),
                    "timeframes": parse_timeframes(content),
                }
            )
        return list(reversed(entries))[:limit]

    def _find_watch(self, symbol: str) -> WatchSymbol | None:
        for watch in self.watchlist():
            if watch.symbol == symbol:
                return watch
        return None

    # ------------------------------------------------------------------ 维护清理

    def prune_if_due(self, config: MarketConfig | None = None) -> dict[str, int]:
        """按 market.retention_days 每天清理一次。

        行情库的保留期独立于 maintenance.retention_days，所以由本服务自己按日执行，
        不反向依赖 MaintenanceService。
        """
        active = config or self._config
        today = time.strftime("%Y-%m-%d")
        if self._store.get_meta(LAST_PRUNE_KEY) == today:
            return {}
        self._store.set_meta(LAST_PRUNE_KEY, today)
        removed = self._store.prune(active.retention_days, min_bars_per_series=active.history_limit)
        if any(removed.values()):
            self._log(f"market prune removed {removed} with retention_days={active.retention_days}")
        return removed

    def prune(self, retention_days: int | None = None) -> dict[str, int]:
        days = retention_days or self._config.retention_days
        return self._store.prune(days, min_bars_per_series=self._config.history_limit)


SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "⚪"}
SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}


def _telegram_text(event: MarketEvent) -> str:
    """手机上读的一小段：发生了什么、什么价位、多严重。"""
    icon = SEVERITY_ICON.get(event.severity, "⚪")
    label = SEVERITY_LABEL.get(event.severity, event.severity)
    lines = [f"{icon} {event.symbol} {event.title}"]
    if event.detail:
        lines.append(event.detail)
    if event.price:
        lines.append(f"现价 {event.price:,.6g} · 严重度 {label}")
    else:
        lines.append(f"严重度 {label}")
    lines.append(event.at.replace("T", " "))
    return "\n".join(lines)


def build_analysis_prompt(snapshot: SymbolSnapshot, now_ts: float) -> str:
    """把平台算好的快照交给 Agent，并要求它不要再自己去取行情。"""
    lines = [
        f"请分析 {snapshot.symbol} {snapshot.alias} 的行情。",
        "",
        f"下面这份快照由平台代码在 {_now_text()} 计算，指标和信号是确定性的，"
        "请把它当作唯一行情来源，不要再去获取行情数据。",
        "",
        f"现价 {_format_price(snapshot.last_price)}（24h {snapshot.change_pct_24h:+.2f}%，"
        f"24h 高 {_format_price(snapshot.high_24h)}，低 {_format_price(snapshot.low_24h)}）",
        "",
    ]
    fast = [frame for frame in snapshot.timeframes if frame.fast]
    slow = [frame for frame in snapshot.timeframes if not frame.fast]
    if fast:
        lines.append("【快层：平台代码秒级计算，仅作背景，不要据此给出需要秒级执行的判断】")
        lines.extend(_frame_lines(fast))
        lines.append("")
    if slow:
        lines.append("【慢层：本次分析的主要对象】")
        lines.extend(_frame_lines(slow))
        lines.append("")
    lines.extend(
        [
            "要求：",
            "1. 只对 1h 及以上的周期给出判断，分钟级数据只用来解释当下发生了什么。",
            "2. 明确指出跨周期的矛盾或一致（例如短周期突破、但长周期仍在下跌通道）。",
            "3. 给出关键价位和需要观察的条件，不要给出下单指令或仓位建议。",
            "4. 按下面结构输出，不要添加其它一级标题：",
            "结论：<一到两句，说清现在是什么状态>",
            "置信度：高|中|低",
            "要点：",
            "- <关键位>",
            "- <矛盾点>",
            "- <需要观察的条件>",
        ]
    )
    return "\n".join(lines)


def _frame_lines(frames: list[TimeframeSnapshot]) -> list[str]:
    lines = []
    for frame in frames:
        state = "进行中" if not frame.closed else "已收盘"
        signals = "、".join(frame.signals) if frame.signals else "无"
        lines.append(
            f"- {frame.timeframe}（{state}）：收盘 {_format_price(frame.last_close)} "
            f"{frame.change_pct:+.2f}%，趋势 {frame.trend}，动量 {frame.momentum}"
            f"（RSI {_optional(frame.indicators.rsi14)}），波动 {frame.volatility}"
            f"（ATR {_optional(frame.indicators.atr14)}），量能 {frame.volume_level}"
            f"（量比 {_optional(frame.indicators.volume_ratio, 2)}x），信号 {signals}；"
            f"近 20 根高点 {_optional(frame.indicators.swing_high)}、"
            f"低点 {_optional(frame.indicators.swing_low)}"
        )
    return lines


def parse_confidence(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped.startswith("置信度"):
            value = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
            for level in ("高", "中", "低"):
                if value.startswith(level):
                    return level
    return ""


def parse_timeframes(text: str) -> list[str]:
    return [timeframe for timeframe in TIMEFRAME_SECONDS if timeframe in text]


def _format_price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}".rstrip("0")


def _optional(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
