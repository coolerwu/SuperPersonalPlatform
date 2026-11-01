from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, time, timezone

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError

from app.config import settings
from app.schemas import BacktestCreate
from app.services import backtest_service
from app.services.backtest_worker import get_active_worker
from app.services.market_data import market_service
from app.services.skill_tools import (
    SKILL_TOOL_REGISTRY,
    SkillToolContext,
    SkillToolRegistry,
)
from app.services.timeframes import granularity_label, validate_granularity

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
STATUS_LABELS = {
    "queued": "排队中",
    "fetching": "抓取行情",
    "running": "回测中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}
MAX_RESULT_CHARS = 12_000
MAX_TRADE_ROWS = 200
DEFAULT_TRADE_ROWS = 30


def _parse_utc_date(value: str) -> int:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"日期必须是 YYYY-MM-DD（UTC），收到 {value!r}") from exc
    return int(
        datetime.combine(parsed, time.min, tzinfo=timezone.utc).timestamp() * 1000
    )


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        messages = []
        for error in exc.errors()[:5]:
            location = ".".join(str(part) for part in error.get("loc", ()))
            message = str(error.get("msg") or "invalid")
            messages.append(f"{location}: {message}" if location else message)
        return "；".join(messages)
    return str(exc)


def _format_pct(value: object) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    number = float(value)
    if not math.isfinite(number):
        return "—"
    return f"{number * 100:.2f}%"


def _format_number(value: object) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    number = float(value)
    if not math.isfinite(number):
        return "—"
    return f"{number:.2f}"


def _format_time(ms: object) -> str:
    if not isinstance(ms, (int, float)):
        return "—"
    return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )


def _metric_lines(row) -> list[str]:
    metrics = row.metrics or {}
    bars = metrics.get("bars") or row.bar_count
    start_ms = metrics.get("start_ms") or row.data_start_ms
    end_ms = metrics.get("end_ms") or row.data_end_ms
    trades = metrics.get("trades")
    if trades is None:
        trades = row.trades_total
    lines = [
        f"数据区间：{_format_time(start_ms)} → {_format_time(end_ms)} UTC，"
        f"共 {bars} 根 K 线",
        f"总收益：{_format_pct(metrics.get('total_return'))}；"
        f"年化：{_format_pct(metrics.get('annual_return'))}；"
        f"最大回撤：{_format_pct(metrics.get('max_drawdown'))}",
        f"Sharpe：{_format_number(metrics.get('sharpe'))}；"
        f"Sortino：{_format_number(metrics.get('sortino'))}；"
        f"胜率：{_format_pct(metrics.get('win_rate'))}；交易：{trades} 笔",
        f"基准买入持有：{_format_pct(metrics.get('benchmark_return'))}；"
        f"盈亏比：{_format_number(metrics.get('profit_factor'))}；"
        f"平均暴露：{_format_pct(metrics.get('avg_exposure'))}",
    ]
    return lines


def _summary_text(row) -> str:
    lines = [
        f"回测 {row.id}：{row.symbol} {granularity_label(row.granularity)} "
        f"{STATUS_LABELS.get(row.status, row.status)}"
    ]
    progress = row.progress or {}
    if row.status in ("queued", "fetching", "running"):
        fetched = progress.get("fetched_bars")
        if fetched:
            lines.append(f"已抓取行情 {fetched} 根，任务仍在执行。")
        lines.append("稍后用 backtest_status 查询，不要重复创建相同回测。")
    if row.status == "completed":
        lines.extend(_metric_lines(row))
    if row.error:
        lines.append(f"错误：{row.error}")
    if row.error_detail and row.status == "failed":
        detail = str(row.error_detail).strip()
        if detail:
            lines.append("错误详情：" + detail[-1500:])
    warnings = list(row.warnings or [])
    if warnings:
        lines.append("告警：" + "；".join(str(item) for item in warnings))
    return "\n".join(lines)[:MAX_RESULT_CHARS]


def _result_text(row, trade_limit: int) -> str:
    lines = [_summary_text(row)]
    trades = list(row.trades or [])
    if trades:
        selected = trades[-trade_limit:]
        total = row.trades_total or len(trades)
        lines.append(f"交易明细（共 {total} 笔，展示最近 {len(selected)} 笔）：")
        for trade in selected:
            side = "做多" if trade.get("side") == "long" else "做空"
            lines.append(
                f"- {_format_time(trade.get('entry_ts'))} → "
                f"{_format_time(trade.get('exit_ts'))} {side}，"
                f"收益 {_format_pct(trade.get('return'))}，"
                f"持有 {trade.get('bars')} 根，"
                f"{'已平仓' if trade.get('closed') else '未平仓'}"
            )
    if row.trades_truncated:
        lines.append("交易明细已截断，只保留最近一部分。")
    return "\n".join(lines)[:MAX_RESULT_CHARS]


async def _run_backtest(
    context: SkillToolContext,
    symbol: str,
    granularity: str,
    start: str,
    end: str,
    strategy_code: str,
    initial_capital: float,
    fee_pct: float,
    slippage_pct: float,
) -> str:
    del context
    try:
        start_ms = _parse_utc_date(start)
        end_ms = _parse_utc_date(end)
        payload = BacktestCreate(
            symbol=symbol,
            granularity=granularity,
            start_ms=start_ms,
            end_ms=end_ms,
            initial_capital=initial_capital,
            fee_rate=max(float(fee_pct), 0.0) / 100.0,
            slippage_rate=max(float(slippage_pct), 0.0) / 100.0,
            strategy_code=strategy_code,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return f"Error: {_validation_message(exc)}"

    row = await backtest_service.create_backtest(payload)
    worker = get_active_worker()
    if worker is not None:
        worker.wake()
    loop = asyncio.get_running_loop()
    wait_seconds = max(0, int(settings.backtest_tool_wait_seconds))
    deadline = loop.time() + wait_seconds
    while True:
        current = await backtest_service.get_backtest(row.id)
        if current is None:
            return f"Error: 回测任务 {row.id} 不存在"
        if current.status in TERMINAL_STATUSES:
            return _summary_text(current)
        if loop.time() >= deadline:
            return _summary_text(current)
        await asyncio.sleep(min(1.0, max(deadline - loop.time(), 0.05)))


async def _get_backtest_status(backtest_id: str) -> str:
    row = await backtest_service.get_backtest(str(backtest_id).strip())
    if row is None:
        return f"Error: 回测任务 {backtest_id} 不存在"
    return _summary_text(row)


async def _get_backtest_result(backtest_id: str, trade_limit: int) -> str:
    row = await backtest_service.get_backtest(str(backtest_id).strip())
    if row is None:
        return f"Error: 回测任务 {backtest_id} 不存在"
    if row.status != "completed":
        return f"回测尚未完成。当前状态：{STATUS_LABELS.get(row.status, row.status)}。\n" + _summary_text(row)
    try:
        limit = min(max(int(trade_limit), 1), MAX_TRADE_ROWS)
    except (TypeError, ValueError):
        limit = DEFAULT_TRADE_ROWS
    return _result_text(row, limit)


async def _cancel_backtest(backtest_id: str) -> str:
    identifier = str(backtest_id).strip()
    worker = get_active_worker()
    try:
        if worker is not None:
            cancelled = await worker.cancel(identifier)
        else:
            cancelled = await backtest_service.cancel_backtest(identifier)
    except LookupError:
        return f"Error: 回测任务 {identifier} 不存在"
    if cancelled:
        return f"已请求取消回测 {identifier}。"
    return f"回测 {identifier} 已经结束，无法取消。"


async def _probe_market(symbol: str, granularity: str) -> str:
    try:
        validate_granularity(granularity)
    except ValueError as exc:
        return f"Error: {exc}"
    ok, latency, error = await market_service.probe(symbol, granularity)
    if ok:
        suffix = f"（{latency} ms）" if latency is not None else ""
        return f"Bitget 连接正常{suffix}。"
    return f"Bitget 连接失败：{error or '未知错误'}。请检查网络或 HTTP_PROXY/HTTPS_PROXY 设置。"


async def _market_cache_status(symbol: str, granularity: str) -> str:
    try:
        status = await market_service.status(symbol, granularity)
    except ValueError as exc:
        return f"Error: {exc}"
    if status.rows == 0:
        return f"{status.symbol} {granularity_label(status.granularity)} 尚未缓存，首次回测会自动抓取。"
    lines = [
        f"{status.symbol} {granularity_label(status.granularity)} 已缓存 "
        f"{status.rows} 根：{_format_time(status.first_ms)} → "
        f"{_format_time(status.last_ms)} UTC"
    ]
    if status.oldest_available_ms:
        lines.append(f"Bitget 最早可用：{_format_time(status.oldest_available_ms)} UTC")
    if status.last_error:
        lines.append(f"上次抓取错误：{status.last_error}")
    return "\n".join(lines)


def _make_run_tool(context: SkillToolContext) -> BaseTool:
    async def backtest_run(
        symbol: str,
        granularity: str,
        start: str,
        end: str,
        strategy_code: str,
        initial_capital: float = 10_000.0,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
    ) -> str:
        """创建一次加密货币回测，并最多等待一段时间取回结果。"""
        return await _run_backtest(
            context,
            symbol,
            granularity,
            start,
            end,
            strategy_code,
            initial_capital,
            fee_pct,
            slippage_pct,
        )

    return StructuredTool.from_function(
        name="backtest_run",
        description=(
            "创建并等待一次 Bitget 现货单标的回测。symbol 例如 BTCUSDT；"
            "granularity 例如 1h、4h、1day；start/end 为 UTC 日期 YYYY-MM-DD，"
            "end 不含。strategy_code 必须定义 def strategy(df) 并返回 [-1,1] "
            "目标仓位（UTC DatetimeIndex，列 open/high/low/close/volume）。"
            "fee_pct/slippage_pct 是百分比，默认 0.1 和 0.05。"
            "最多等待一段时间；完成返回指标摘要，仍在执行时返回任务 id，"
            "之后用 backtest_status 查询。"
        ),
        coroutine=backtest_run,
    )


def _make_status_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def backtest_status(backtest_id: str) -> str:
        """查询回测任务状态、进度和已完成指标。"""
        return await _get_backtest_status(backtest_id)

    return StructuredTool.from_function(
        name="backtest_status",
        description="查询回测任务的状态、进度、错误和已完成指标；参数是 backtest_run 返回的任务 id。",
        coroutine=backtest_status,
    )


def _make_result_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def backtest_result(
        backtest_id: str, trade_limit: int = DEFAULT_TRADE_ROWS
    ) -> str:
        """读取已完成回测的指标和最近交易明细。"""
        return await _get_backtest_result(backtest_id, trade_limit)

    return StructuredTool.from_function(
        name="backtest_result",
        description=(
            "读取已完成回测的指标、告警和最近交易明细；不返回完整净值或价格数组。"
            f"trade_limit 默认 {DEFAULT_TRADE_ROWS}，上限 {MAX_TRADE_ROWS}。"
            "任务未完成时返回当前状态。"
        ),
        coroutine=backtest_result,
    )


def _make_cancel_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def backtest_cancel(backtest_id: str) -> str:
        """取消排队或执行中的回测任务。"""
        return await _cancel_backtest(backtest_id)

    return StructuredTool.from_function(
        name="backtest_cancel",
        description="取消排队或执行中的回测任务；参数是 backtest_run 返回的任务 id。",
        coroutine=backtest_cancel,
    )


def _make_probe_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def market_probe(symbol: str, granularity: str) -> str:
        """测试 Bitget 行情连接和交易对。"""
        return await _probe_market(symbol, granularity)

    return StructuredTool.from_function(
        name="market_probe",
        description=(
            "测试 Bitget 公共行情连接和交易对是否可用。symbol 例如 BTCUSDT，"
            "granularity 例如 1h；返回连接延迟或中文错误。"
        ),
        coroutine=market_probe,
    )


def _make_cache_status_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def market_cache_status(symbol: str, granularity: str) -> str:
        """查看本地行情缓存范围。"""
        return await _market_cache_status(symbol, granularity)

    return StructuredTool.from_function(
        name="market_cache_status",
        description=(
            "查看某个交易对和周期在本地 market.db 中已缓存的范围、最早可用时间和上次抓取错误。"
        ),
        coroutine=market_cache_status,
    )


def register_backtest_tools(
    registry: SkillToolRegistry = SKILL_TOOL_REGISTRY,
) -> None:
    specs = (
        ("backtest_run", _make_run_tool, "创建并等待一次加密货币回测"),
        ("backtest_status", _make_status_tool, "查询回测任务状态与指标"),
        ("backtest_result", _make_result_tool, "读取回测指标与最近交易"),
        ("backtest_cancel", _make_cancel_tool, "取消回测任务"),
        ("market_probe", _make_probe_tool, "测试 Bitget 行情连接"),
        ("market_cache_status", _make_cache_status_tool, "查看本地行情缓存范围"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_backtest_tools()
