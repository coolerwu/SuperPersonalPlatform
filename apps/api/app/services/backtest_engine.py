"""Vectorised single-symbol backtest engine.

The strategy sees bars up to and including bar ``t`` and returns a target
position for the *next* bar; the engine shifts the signal by one bar so a
decision can never use the return it is about to earn.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from app.services.timeframes import (
    INTERVAL_MS,
    count_bars,
    next_open_ms,
    periods_per_year,
)

COLUMNS = ["open", "high", "low", "close", "volume"]


class BacktestError(Exception):
    """A failure that should be shown to the user verbatim."""


def load_candles(
    db_path: str | Path,
    symbol: str,
    granularity: str,
    start_ms: int,
    end_open_ms: int,
) -> pd.DataFrame:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA query_only=1")
        connection.execute("PRAGMA busy_timeout=5000")
        frame = pd.read_sql_query(
            """
            SELECT open_time_ms, open, high, low, close, base_volume
            FROM market_candles
            WHERE symbol = ? AND granularity = ?
              AND open_time_ms >= ? AND open_time_ms < ?
            ORDER BY open_time_ms
            """,
            connection,
            params=(symbol, granularity, start_ms, end_open_ms),
        )
    finally:
        connection.close()
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    frame = frame.rename(columns={"base_volume": "volume"})
    frame.index = pd.to_datetime(frame.pop("open_time_ms"), unit="ms", utc=True)
    frame.index.name = "time"
    return frame[COLUMNS].astype(float)


def validate_signals(signals: object, bars: int) -> tuple[pd.Series, list[str]]:
    warnings: list[str] = []
    if callable(signals):
        raise BacktestError("策略必须返回仓位序列，而不是函数")
    try:
        raw = np.asarray(signals.to_numpy() if hasattr(signals, "to_numpy") else signals)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        raise BacktestError("无法读取策略返回值，请返回 pandas Series") from exc
    if raw.ndim != 1:
        raise BacktestError("策略必须返回一维仓位序列（长度与 K 线一致）")
    if len(raw) != bars:
        raise BacktestError(
            f"策略返回长度 {len(raw)} 与行情长度 {bars} 不一致"
        )
    values = pd.to_numeric(pd.Series(raw), errors="coerce")
    nan_count = int(values.isna().sum())
    if nan_count:
        warnings.append(f"{nan_count} 根 K 线的仓位缺失，已按空仓处理")
    values = values.fillna(0.0).astype(float)
    clipped = int(((values < -1) | (values > 1)).sum())
    if clipped:
        warnings.append(f"{clipped} 根 K 线的仓位超出 [-1, 1]，已裁剪")
    values = values.clip(-1.0, 1.0)
    return values, warnings


def run_backtest(
    frame: pd.DataFrame,
    signals: object,
    *,
    initial_capital: float,
    fee_rate: float,
    slippage_rate: float,
    granularity: str,
    max_trades: int,
    max_points: int,
    expected_bars: int | None = None,
) -> dict:
    if len(frame) < 2:
        raise BacktestError("行情数据不足 2 根 K 线，无法回测")
    close = frame["close"].astype(float)
    values, warnings = validate_signals(signals, len(frame))

    position = pd.Series(values.to_numpy(), index=frame.index)
    applied = position.shift(1).fillna(0.0)
    asset_return = close.pct_change().fillna(0.0)
    turnover = applied.diff().abs()
    if len(turnover):
        turnover.iloc[0] = abs(float(applied.iloc[0]))
    cost_rate = float(fee_rate) + float(slippage_rate)
    cost = turnover * cost_rate
    net_return = applied * asset_return - cost
    growth = 1.0 + net_return
    ruined = bool((growth <= 0).any())
    if ruined:
        warnings.append("净值曾触及 0，已按最小净值截断")
        growth = growth.clip(lower=1e-9)
    equity = initial_capital * growth.cumprod()
    drawdown = equity / equity.cummax() - 1.0

    if (applied < 0).any():
        warnings.append(
            "策略使用了负仓位（模拟做空），未计入借币成本、资金费率与强平风险"
        )
    if expected_bars is not None and expected_bars > len(frame):
        warnings.append(
            f"缓存缺少 {expected_bars - len(frame)} 根 K 线，回测只使用实际存在的 {len(frame)} 根"
        )
    if len(frame) < 30:
        warnings.append("样本少于 30 根 K 线，收益与风险指标仅供参考")

    trades = build_trades(
        frame,
        applied,
        initial_capital=initial_capital,
        cost_rate=cost_rate,
        max_trades=max_trades,
    )
    metrics = compute_metrics(
        frame,
        net_return,
        equity,
        drawdown,
        trades,
        initial_capital=initial_capital,
        granularity=granularity,
        avg_cost=float(cost.sum()),
        avg_exposure=float(applied.abs().mean()),
    )
    times = [int(value.timestamp() * 1000) for value in frame.index]
    equity_points = downsample_lttb(
        times, [float(value) for value in equity.to_numpy()], max_points
    )
    drawdown_points = downsample_lttb(
        times, [float(value) for value in drawdown.to_numpy()], max_points
    )
    candles = downsample_ohlc(frame, max_points)
    return {
        "metrics": json_safe(metrics),
        "equity": json_safe(equity_points),
        "drawdown": json_safe(drawdown_points),
        "candles": json_safe(candles),
        "trades": json_safe(trades["items"]),
        "trades_total": trades["total"],
        "trades_truncated": trades["truncated"],
        "warnings": warnings,
    }


def compute_metrics(
    frame: pd.DataFrame,
    net_return: pd.Series,
    equity: pd.Series,
    drawdown: pd.Series,
    trades: dict,
    *,
    initial_capital: float,
    granularity: str,
    avg_cost: float,
    avg_exposure: float,
) -> dict:
    final_equity = float(equity.iloc[-1])
    total_return = final_equity / initial_capital - 1.0
    elapsed_seconds = (
        frame.index[-1] - frame.index[0]
    ).total_seconds() + _bar_seconds(granularity)
    years = elapsed_seconds / (365.25 * 24 * 3600)
    annual_return = _annualize(total_return, years)
    per_year = periods_per_year(granularity)
    std = float(net_return.std(ddof=1)) if len(net_return) > 1 else 0.0
    sharpe = float(net_return.mean()) / std * math.sqrt(per_year) if std > 0 else None
    downside = net_return[net_return < 0]
    downside_std = (
        float(np.sqrt((downside**2).mean())) if len(downside) else 0.0
    )
    sortino = (
        float(net_return.mean()) / downside_std * math.sqrt(per_year)
        if downside_std > 0
        else None
    )
    close = frame["close"]
    benchmark_return = float(close.iloc[-1] / close.iloc[0] - 1.0)
    benchmark_annual = _annualize(benchmark_return, years)
    trade_returns = [trade["return"] for trade in trades["items"] if trade["return"] is not None]
    wins = [value for value in trade_returns if value > 0]
    losses = [value for value in trade_returns if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "bars": int(len(frame)),
        "start_ms": int(frame.index[0].timestamp() * 1000),
        "end_ms": int(frame.index[-1].timestamp() * 1000),
        "years": years,
        "initial_capital": float(initial_capital),
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "sortino": sortino,
        "volatility": std * math.sqrt(per_year) if std > 0 else None,
        "win_rate": (len(wins) / len(trade_returns)) if trade_returns else None,
        "trades": trades["total"],
        "profit_factor": profit_factor,
        "benchmark_return": benchmark_return,
        "benchmark_annual_return": benchmark_annual,
        "avg_exposure": avg_exposure,
        "total_cost": avg_cost,
        "avg_trade_return": (
            sum(trade_returns) / len(trade_returns) if trade_returns else None
        ),
    }


def build_trades(
    frame: pd.DataFrame,
    applied: pd.Series,
    *,
    initial_capital: float,
    cost_rate: float,
    max_trades: int,
) -> dict:
    close = frame["close"].to_numpy(dtype=float)
    positions = applied.to_numpy(dtype=float)
    signs = np.sign(positions)
    times = [int(value.timestamp() * 1000) for value in frame.index]
    trades: list[dict] = []
    entry_index: int | None = None
    entry_side = 0.0
    for index in range(len(positions)):
        previous_side = signs[index - 1] if index > 0 else 0.0
        current_side = signs[index]
        if entry_index is not None and current_side != entry_side:
            trades.append(
                _close_trade(
                    entry_index,
                    index,
                    entry_side,
                    close,
                    positions,
                    times,
                    initial_capital,
                    cost_rate,
                    closed=True,
                )
            )
            entry_index = None
        if entry_index is None and current_side != 0 and current_side != previous_side:
            entry_index = index
            entry_side = current_side
    if entry_index is not None:
        trades.append(
            _close_trade(
                entry_index,
                len(positions),
                entry_side,
                close,
                positions,
                times,
                initial_capital,
                cost_rate,
                closed=False,
            )
        )
    total = len(trades)
    truncated = total > max_trades
    items = trades[-max_trades:] if truncated else trades
    return {"items": items, "total": total, "truncated": truncated}


def _close_trade(
    entry_index: int,
    exit_index: int,
    side: float,
    close: np.ndarray,
    positions: np.ndarray,
    times: list[int],
    initial_capital: float,
    cost_rate: float,
    *,
    closed: bool,
) -> dict:
    entry_price_index = max(entry_index - 1, 0)
    entry_price = float(close[entry_price_index])
    if closed:
        exit_price = float(close[max(exit_index - 1, entry_price_index)])
        exit_time = times[max(exit_index - 1, entry_price_index)]
    else:
        exit_price = float(close[-1])
        exit_time = times[-1]
    gross_price = (
        side * (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
    )
    if exit_index > entry_index:
        window = positions[entry_index:exit_index]
        exposure = float(np.mean(np.abs(window))) if len(window) else 0.0
    else:
        exposure = abs(float(positions[entry_index]))
    gross = gross_price * exposure
    entry_cost = cost_rate * abs(float(positions[entry_index]))
    exit_cost = (
        cost_rate * abs(float(positions[max(exit_index - 1, entry_index)]))
        if closed
        else 0.0
    )
    net = gross - entry_cost - exit_cost
    return {
        "entry_ts": times[entry_index],
        "exit_ts": exit_time,
        "side": "long" if side > 0 else "short",
        "entry_price": entry_price,
        "exit_price": exit_price,
        "bars": int(exit_index - entry_index),
        "return": net,
        "pnl": initial_capital * net,
        "closed": closed,
    }


def downsample_ohlc(frame: pd.DataFrame, max_points: int) -> list[list]:
    count = len(frame)
    step = max(1, math.ceil(count / max_points)) if max_points > 0 else 1
    points: list[list] = []
    for start in range(0, count, step):
        chunk = frame.iloc[start : start + step]
        points.append(
            [
                int(chunk.index[0].timestamp() * 1000),
                float(chunk["open"].iloc[0]),
                float(chunk["high"].max()),
                float(chunk["low"].min()),
                float(chunk["close"].iloc[-1]),
                float(chunk["volume"].sum()),
            ]
        )
    return points


def downsample_lttb(
    times: list[int], values: list[float], threshold: int
) -> list[list]:
    """Largest-Triangle-Three-Buckets; keeps spikes that plain sampling loses."""
    count = len(times)
    if threshold <= 2 or count <= threshold:
        return [[time, value] for time, value in zip(times, values)]
    sampled = [[times[0], values[0]]]
    bucket_size = (count - 2) / (threshold - 2)
    anchor = 0
    for bucket in range(threshold - 2):
        avg_start = int(math.floor((bucket + 1) * bucket_size)) + 1
        avg_end = min(int(math.floor((bucket + 2) * bucket_size)) + 1, count)
        avg_x = sum(times[index] for index in range(avg_start, avg_end)) / max(
            avg_end - avg_start, 1
        )
        avg_y = sum(values[index] for index in range(avg_start, avg_end)) / max(
            avg_end - avg_start, 1
        )
        range_start = int(math.floor(bucket * bucket_size)) + 1
        range_end = max(int(math.floor((bucket + 1) * bucket_size)) + 1, range_start + 1)
        range_end = min(range_end, count - 1)
        anchor_x = times[anchor]
        anchor_y = values[anchor]
        best_area = -1.0
        best_index = range_start
        for index in range(range_start, range_end):
            area = abs(
                (anchor_x - avg_x) * (values[index] - anchor_y)
                - (anchor_x - times[index]) * (avg_y - anchor_y)
            )
            if area > best_area:
                best_area = area
                best_index = index
        sampled.append([times[best_index], values[best_index]])
        anchor = best_index
    sampled.append([times[-1], values[-1]])
    return sampled


def json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _annualize(total_return: float, years: float) -> float | None:
    """Annualise a return without overflowing on very short samples."""
    if years <= 0 or total_return <= -1:
        return None
    try:
        value = (1.0 + total_return) ** (1.0 / years) - 1.0
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _bar_seconds(granularity: str) -> float:
    value = INTERVAL_MS[granularity]
    return (value or 30 * 24 * 3600 * 1000) / 1000
