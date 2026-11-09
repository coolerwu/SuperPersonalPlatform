"""Causal Chan signal replay and a simple trade plan.

The drawing page computes structures on a full prefix, which is fine for
inspection but not directly tradeable: a fractal/stroke is only confirmed after
later bars arrive. This module replays the analysis prefix by prefix, records the
first bar at which each buy/sell point becomes visible, and enters on the next
bar's open. It is deliberately a simple plan (one position, fixed stop/R target,
time stop) so the result is easy to inspect and challenge.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config import settings
from app.services.backtest_engine import load_candles
from app.services.chan_analysis import ChanError, analyze_frame
from app.services.market_data import MarketDataError, market_service
from app.services.timeframes import count_bars, floor_open_ms, now_ms, shift_open_ms

MIN_BARS = 60
MAX_BARS = 1000
DEFAULT_INITIAL_CAPITAL = 10_000.0
DEFAULT_FEE_RATE = 0.001
DEFAULT_SLIPPAGE_RATE = 0.0005
DEFAULT_STOP_PCT = 0.01
DEFAULT_RISK_PCT = 0.01
DEFAULT_R_MULTIPLE = 2.0
DEFAULT_MAX_HOLD_BARS = 50
SUPPORTED_GRANULARITIES = ("15min", "1h", "4h", "1day", "1week")
GRANULARITY_ALIASES = {"15m": "15min", "1w": "1week", "1wk": "1week", "1d": "1day"}
SIGNAL_PRIORITY = {
    "1buy": 1,
    "1sell": 1,
    "2buy": 2,
    "2sell": 2,
    "3buy": 3,
    "3sell": 3,
}


class ChanBacktestError(ValueError):
    """A backtest request that should be shown to the user verbatim."""


def _time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _annual_return(initial: float, final: float, years: float) -> float | None:
    if initial <= 0 or final <= 0 or years <= 0:
        return None
    return (final / initial) ** (1.0 / years) - 1.0


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [trade for trade in trades if trade.get("pnl") is not None]
    returns = [float(trade["r_multiple"]) for trade in closed if trade.get("r_multiple") is not None]
    wins = [trade for trade in closed if float(trade.get("pnl") or 0) > 0]
    losses = [trade for trade in closed if float(trade.get("pnl") or 0) < 0]
    gross_profit = sum(float(trade["pnl"]) for trade in wins)
    gross_loss = abs(sum(float(trade["pnl"]) for trade in losses))
    return {
        "trades": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "avg_r": (sum(returns) / len(returns)) if returns else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "total_pnl": sum(float(trade["pnl"]) for trade in closed),
    }


def _signal_key(point: dict[str, Any]) -> tuple[str, int, float]:
    return (
        str(point.get("type")),
        int(point.get("time") or 0),
        round(float(point.get("price") or 0), 10),
    )


def collect_causal_signals(
    frame: pd.DataFrame,
    *,
    warmup_bars: int = 20,
) -> list[dict[str, Any]]:
    """Find the first prefix at which each Chan point becomes visible.

    A point is confirmed at the close of ``confirmed_index``. The caller must
    enter no earlier than the next bar's open. Points that disappear on later
    prefixes remain recorded because they were visible to a live decision.
    """
    if len(frame) < max(3, warmup_bars + 2):
        return []
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, int, float]] = set()
    start = max(2, warmup_bars - 1)
    for index in range(start, len(frame) - 1):
        prefix = frame.iloc[: index + 1]
        try:
            result = analyze_frame(prefix)
        except ChanError:
            continue
        confirmed_time = int(pd.Timestamp(frame.index[index]).timestamp() * 1000)
        for point in result.get("points") or []:
            key = _signal_key(point)
            if key in seen:
                continue
            seen.add(key)
            signals.append(
                {
                    **point,
                    "confirmed_index": index,
                    "confirmed_time": confirmed_time,
                }
            )
    return signals


def simulate_trades(
    frame: pd.DataFrame,
    signals: list[dict[str, Any]],
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    stop_pct: float = DEFAULT_STOP_PCT,
    risk_pct: float = DEFAULT_RISK_PCT,
    r_multiple: float = DEFAULT_R_MULTIPLE,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> dict[str, Any]:
    """Replay signals with one position, fixed stop/target and a time stop."""
    if frame.empty:
        raise ChanBacktestError("没有可回测的 K 线")
    bars = []
    for timestamp, row in frame.iterrows():
        if not all(_finite(row[column]) for column in ("open", "high", "low", "close")):
            continue
        bars.append(
            {
                "time": int(pd.Timestamp(timestamp).timestamp() * 1000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        )
    if len(bars) < 2:
        raise ChanBacktestError("有效 K 线不足 2 根")

    by_confirmation: dict[int, list[dict[str, Any]]] = {}
    for signal in signals:
        by_confirmation.setdefault(int(signal["confirmed_index"]), []).append(signal)
    for group in by_confirmation.values():
        group.sort(key=lambda item: SIGNAL_PRIORITY.get(str(item.get("type")), 99))

    equity = float(initial_capital)
    realized = 0.0
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[list[float]] = []
    signal_rows: list[dict[str, Any]] = []
    skipped = 0
    exposure_bars = 0

    def close_position(
        index: int,
        price: float,
        reason: str,
        *,
        forced: bool = False,
    ) -> None:
        nonlocal equity, realized, position
        assert position is not None
        side = position["side"]
        sign = 1.0 if side == "long" else -1.0
        quantity = position["quantity"]
        exit_fee = abs(price * quantity) * fee_rate
        gross = (price - position["entry_price"]) * quantity * sign
        pnl = gross - position["entry_fee"] - exit_fee
        realized += pnl
        equity = initial_capital + realized
        trade = {
            "signal_type": position["signal_type"],
            "side": side,
            "confirm_time": position["confirm_time"],
            "entry_time": position["entry_time"],
            "entry_price": position["entry_price"],
            "exit_time": bars[index]["time"],
            "exit_price": price,
            "quantity": quantity,
            "stop_loss": position["stop_loss"],
            "take_profit": position["take_profit"],
            "bars_held": index - position["entry_index"],
            "pnl": pnl,
            "r_multiple": pnl / position["risk_amount"] if position["risk_amount"] else None,
            "reason": reason,
            "forced": forced,
        }
        trades.append(trade)
        position = None

    for index, bar in enumerate(bars):
        # Manage an existing position before considering a new entry.
        if position is not None:
            exposure_bars += 1
            entry = position["entry_price"]
            stop = position["stop_loss"]
            target = position["take_profit"]
            long = position["side"] == "long"
            stop_hit = bar["low"] <= stop if long else bar["high"] >= stop
            target_hit = bar["high"] >= target if long else bar["low"] <= target
            if stop_hit:
                exit_price = stop * (1 - slippage_rate) if long else stop * (1 + slippage_rate)
                close_position(index, exit_price, "stop")
            elif target_hit:
                exit_price = target * (1 - slippage_rate) if long else target * (1 + slippage_rate)
                close_position(index, exit_price, "target")
            elif index - int(position["entry_index"]) >= max_hold_bars:
                exit_price = bar["close"] * (1 - slippage_rate) if long else bar["close"] * (1 + slippage_rate)
                close_position(index, exit_price, "time")

        mark = equity
        if position is not None:
            sign = 1.0 if position["side"] == "long" else -1.0
            mark += (bar["close"] - position["entry_price"]) * position["quantity"] * sign
        equity_curve.append([bar["time"], mark])

        if position is not None or index == 0:
            continue
        candidates = by_confirmation.get(index - 1, [])
        if not candidates:
            continue
        signal = candidates[0]
        for extra in candidates[1:]:
            signal_rows.append({**extra, "status": "skipped", "reason": "已有优先信号"})
            skipped += 1
        side = "long" if str(signal["type"]).endswith("buy") else "short"
        entry_price = bar["open"] * (1 + slippage_rate) if side == "long" else bar["open"] * (1 - slippage_rate)
        signal_price = float(signal["price"])
        stop = signal_price * (1 - stop_pct) if side == "long" else signal_price * (1 + stop_pct)
        if (side == "long" and stop >= entry_price) or (side == "short" and stop <= entry_price):
            stop = entry_price * (1 - stop_pct) if side == "long" else entry_price * (1 + stop_pct)
        risk_per_unit = abs(entry_price - stop)
        if risk_per_unit <= 0:
            signal_rows.append({**signal, "status": "skipped", "reason": "止损距离为 0"})
            skipped += 1
            continue
        risk_amount = equity * risk_pct
        quantity = min(risk_amount / risk_per_unit, equity / entry_price)
        if quantity <= 0:
            signal_rows.append({**signal, "status": "skipped", "reason": "仓位为 0"})
            skipped += 1
            continue
        take_profit = (
            entry_price + r_multiple * risk_per_unit
            if side == "long"
            else entry_price - r_multiple * risk_per_unit
        )
        entry_fee = abs(entry_price * quantity) * fee_rate
        position = {
            "side": side,
            "signal_type": signal["type"],
            "confirm_time": signal["confirmed_time"],
            "entry_time": bar["time"],
            "entry_index": index,
            "entry_price": entry_price,
            "quantity": quantity,
            "stop_loss": stop,
            "take_profit": take_profit,
            "risk_amount": risk_amount,
            "entry_fee": entry_fee,
        }
        signal_rows.append({**signal, "status": "traded", "reason": "已按计划进场"})

    if position is not None:
        close_position(len(bars) - 1, bars[-1]["close"], "end", forced=True)

    values = [value for _, value in equity_curve]
    initial_time = bars[0]["time"]
    final_time = bars[-1]["time"]
    years = max((final_time - initial_time) / (365.25 * 24 * 3600 * 1000), 1 / 365.25)
    final_equity = values[-1] if values else initial_capital
    stats = _stats(trades)
    by_type = []
    for signal_type in ("1buy", "2buy", "3buy", "1sell", "2sell", "3sell"):
        group = [trade for trade in trades if trade["signal_type"] == signal_type]
        if group:
            by_type.append({"type": signal_type, **_stats(group)})
    warnings: list[str] = []
    if stats["trades"] < 20:
        warnings.append("成交样本少于 20 笔，统计结果不稳定，不要据此下重注")
    if not trades:
        warnings.append("这段样本没有触发可执行的交易计划")
    warnings.append("因果前缀回放是近似：信号在收盘确认，下一根开盘进场；不是实盘成交")
    warnings.append("做空为现货模拟，不含借币成本、资金费率和强平")
    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return": final_equity / initial_capital - 1.0,
        "annual_return": _annual_return(initial_capital, final_equity, years),
        "max_drawdown": _max_drawdown(values),
        "win_rate": stats["win_rate"],
        "avg_r": stats["avg_r"],
        "profit_factor": stats["profit_factor"],
        "trades": len(trades),
        "total_pnl": stats["total_pnl"],
        "exposure": exposure_bars / len(bars),
        "by_type": by_type,
        "trades_detail": trades,
        "signals": signal_rows,
        "skipped_signals": skipped,
        "equity": equity_curve,
        "bars": len(bars),
        "start_ms": initial_time,
        "end_ms": final_time,
        "warnings": warnings,
    }


async def run_backtest(
    symbol: str,
    granularity: str,
    *,
    bars: int = 500,
    start_ms: int | None = None,
    end_ms: int | None = None,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    stop_pct: float = DEFAULT_STOP_PCT,
    risk_pct: float = DEFAULT_RISK_PCT,
    r_multiple: float = DEFAULT_R_MULTIPLE,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    granularity = GRANULARITY_ALIASES.get(str(granularity).strip().lower(), str(granularity).strip())
    if not symbol:
        raise ChanBacktestError("交易对不能为空")
    if granularity not in SUPPORTED_GRANULARITIES:
        raise ChanBacktestError("只支持 15min、1h、4h、1day、1week")
    if not 0 < stop_pct < 0.2:
        raise ChanBacktestError("止损百分比必须在 0-20% 之间")
    if not 0 < risk_pct <= 0.1:
        raise ChanBacktestError("单笔风险必须在 0-10% 之间")
    if r_multiple <= 0:
        raise ChanBacktestError("盈亏比必须大于 0")
    if not 1 <= max_hold_bars <= 1000:
        raise ChanBacktestError("最大持仓 K 线必须在 1-1000 之间")

    now_open = floor_open_ms(now_ms(), granularity)
    if end_ms is not None:
        end_open = min(floor_open_ms(int(end_ms), granularity), now_open)
    else:
        end_open = now_open
    if start_ms is not None:
        window_start = floor_open_ms(int(start_ms), granularity)
    else:
        if not MIN_BARS <= bars <= MAX_BARS:
            raise ChanBacktestError(f"回测 K 线必须在 {MIN_BARS}-{MAX_BARS} 根之间")
        window_start = shift_open_ms(end_open, granularity, -bars)
    if end_open <= window_start:
        raise ChanBacktestError("所选时间窗口内没有已经完成的 K 线")
    wanted = count_bars(window_start, end_open, granularity)
    if wanted > MAX_BARS:
        raise ChanBacktestError(
            f"所选时间窗口约 {wanted} 根 K 线，超过上限 {MAX_BARS}；请缩短窗口或换更大的周期"
        )
    if wanted < MIN_BARS:
        raise ChanBacktestError(f"所选时间窗口只有 {wanted} 根 K 线，至少需要 {MIN_BARS} 根")
    try:
        ensure = await market_service.ensure_data(symbol, granularity, window_start, end_open)
    except MarketDataError as exc:
        raise ChanBacktestError(str(exc)) from exc
    frame = load_candles(settings.market_db_path, symbol, granularity, window_start, ensure.end_open_ms)
    if frame.empty:
        raise ChanBacktestError("行情缓存为空，请先检查 Bitget 连接或换一个交易对/周期")
    signals = collect_causal_signals(frame)
    result = simulate_trades(
        frame,
        signals,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        stop_pct=stop_pct,
        risk_pct=risk_pct,
        r_multiple=r_multiple,
        max_hold_bars=max_hold_bars,
    )
    result.update(
        {
            "symbol": symbol,
            "granularity": granularity,
            "requested_bars": wanted,
            "causal_signals": len(signals),
            "data_warnings": ensure.warnings,
        }
    )
    return result


def format_result(result: dict[str, Any]) -> str:
    """Compact summary for the Agent tool."""
    lines = [
        f"## 缠论信号回测 · {result.get('symbol')} {result.get('granularity')}",
        f"- 区间：{_time(result.get('start_ms'))} → {_time(result.get('end_ms'))} UTC；"
        f"{result.get('bars')} 根 K 线；因果信号 {result.get('causal_signals')} 个",
        f"- 交易：{result.get('trades')} 笔；胜率 {_pct(result.get('win_rate'))}；"
        f"平均 R {_num(result.get('avg_r'))}；盈亏比 {_num(result.get('profit_factor'))}",
        f"- 总收益 {_pct(result.get('total_return'))}；最大回撤 {_pct(result.get('max_drawdown'))}；"
        f"累计盈亏 {_money(result.get('total_pnl'))} {result.get('initial_capital')}",
    ]
    by_type = result.get("by_type") or []
    if by_type:
        lines.append("\n### 按信号类型")
        for group in by_type:
            lines.append(
                f"- {group['type']}：{group['trades']} 笔，胜率 {_pct(group.get('win_rate'))}，"
                f"平均 R {_num(group.get('avg_r'))}，盈亏 {_money(group.get('total_pnl'))}"
            )
    trades = result.get("trades_detail") or []
    if trades:
        lines.append("\n### 最近交易")
        for trade in trades[-8:]:
            lines.append(
                f"- {trade['signal_type']} {trade['side']}：{_time(trade['entry_time'])} "
                f"{_price(trade['entry_price'])} → {_time(trade['exit_time'])} "
                f"{_price(trade['exit_price'])}，R {_num(trade.get('r_multiple'))}，"
                f"原因 {trade.get('reason')}"
            )
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("\n### 提示")
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("\n这是回测和交易计划，不是收益承诺；样本不足时不要据此交易。")
    return "\n".join(lines)


def _pct(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:+.2f}%"


def _num(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.2f}"


def _money(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):+,.2f}"


def _price(value: object) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:,.8f}".rstrip("0").rstrip(".")
