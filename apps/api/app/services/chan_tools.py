"""Agent tool for the deterministic Chan-theory analysis page."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, StructuredTool

from app.services import chan_analysis, chan_backtest
from app.services.skill_tools import (
    SKILL_TOOL_REGISTRY,
    SkillToolContext,
    SkillToolRegistry,
)

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 12_000
MAX_STROKES = 8
MAX_ZONES = 10
MAX_POINTS = 24

POINT_LABELS = {
    "1buy": "1买",
    "2buy": "2买",
    "3buy": "3买",
    "1sell": "1卖",
    "2sell": "2卖",
    "3sell": "3卖",
}


def _time(ms: object) -> str:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (TypeError, ValueError, OSError):
        return "—"


def _price(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.8f}".rstrip("0").rstrip(".")


def _format(result: dict) -> str:
    rules = result.get("rules") or {}
    lines = [
        f"## 缠论结构 · {result.get('symbol')} {result.get('granularity')}",
        f"- 区间：{_time(result.get('start_ms'))} → {_time(result.get('end_ms'))} UTC",
        f"- 原始 K 线：{result.get('bars', 0)}；合并 K 线：{result.get('merged_bars', 0)}；"
        f"笔：{result.get('stroke_count', 0)}；中枢：{result.get('zone_count', 0)}；"
        f"买卖点：{result.get('point_count', 0)}",
        f"- 规则：{rules.get('name', '程序化缠论 · 老笔')}；{rules.get('bi_rule', '')}",
        f"- 买卖点口径：{rules.get('point_rule', '')}",
    ]
    zones = result.get("zones") or []
    if zones:
        lines.append("\n### 中枢")
        for zone in zones[:MAX_ZONES]:
            lines.append(
                f"- {_time(zone.get('start_time'))} → {_time(zone.get('end_time'))} UTC："
                f"ZG {_price(zone.get('zg'))} / ZD {_price(zone.get('zd'))}"
            )
        if len(zones) > MAX_ZONES:
            lines.append(f"- 还有 {len(zones) - MAX_ZONES} 个中枢未列出")
    else:
        lines.append("\n### 中枢\n- 当前样本没有形成有效中枢")

    strokes = result.get("strokes") or []
    if strokes:
        lines.append("\n### 最近几笔")
        for stroke in strokes[-MAX_STROKES:]:
            direction = "向上" if stroke.get("direction") == "up" else "向下"
            lines.append(
                f"- {direction}：{_time(stroke.get('start_time'))} {_price(stroke.get('start_price'))} → "
                f"{_time(stroke.get('end_time'))} {_price(stroke.get('end_price'))}"
            )

    points = result.get("points") or []
    if points:
        lines.append("\n### 买卖点")
        for point in points[:MAX_POINTS]:
            lines.append(
                f"- {POINT_LABELS.get(str(point.get('type')), point.get('type'))}："
                f"{_time(point.get('time'))} {_price(point.get('price'))}；{point.get('reason')}"
            )
        if len(points) > MAX_POINTS:
            lines.append(f"- 还有 {len(points) - MAX_POINTS} 个买卖点未列出")
    else:
        lines.append("\n### 买卖点\n- 当前样本没有触发笔级别买卖点")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("\n### 提示")
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append(
        "\n注意：这是程序化老笔近似，不是唯一缠论标准；买卖点不是交易建议，"
        "需要结合线段、背驰和风险控制判断。"
    )
    output = "\n".join(lines)
    if len(output) > MAX_RESULT_CHARS:
        output = output[:MAX_RESULT_CHARS] + "\n... [结果过大已截断]"
    return output


def _make_analyze_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def chan_analyze(
        symbol: str,
        granularity: str = "1h",
        bars: int = 300,
    ) -> str:
        """分析一个交易对的缠论结构。"""
        try:
            result = await chan_analysis.analyze_symbol(
                symbol,
                granularity,
                bars=bars,
            )
        except chan_analysis.ChanError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            logger.exception("chan analysis tool failed")
            return f"Error: 缠论分析失败：{exc}"
        return _format(result)

    return StructuredTool.from_function(
        name="chan_analyze",
        description=(
            "分析 crypto 单周期的程序化缠论结构。symbol 例如 BTCUSDT；"
            "granularity 支持 15min、1h、4h、1day、1week（也接受 15m/1w）；bars 默认 300，范围 20-1000。"
            "返回数据区间、合并 K 线数、笔、中枢、1/2/3 买卖点和 warnings 的摘要，"
            "不返回完整 K 线数组。只使用已收盘 K 线；买卖点是笔级别近似，"
            "不是交易建议。用户要求看缠论结构或解释分型/笔/中枢/买卖点时使用。"
        ),
        coroutine=chan_analyze,
    )


def _make_backtest_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def chan_backtest_tool(
        symbol: str,
        granularity: str = "1h",
        bars: int = 300,
        stop_pct: float = 1.0,
        risk_pct: float = 1.0,
        r_multiple: float = 2.0,
        max_hold_bars: int = 50,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
        initial_capital: float = 10_000.0,
    ) -> str:
        """用因果前缀回放回测缠论买卖点，并返回交易计划摘要。"""
        try:
            result = await chan_backtest.run_backtest(
                symbol,
                granularity,
                bars=bars,
                initial_capital=initial_capital,
                fee_rate=fee_pct / 100.0,
                slippage_rate=slippage_pct / 100.0,
                stop_pct=stop_pct / 100.0,
                risk_pct=risk_pct / 100.0,
                r_multiple=r_multiple,
                max_hold_bars=max_hold_bars,
            )
        except chan_backtest.ChanBacktestError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            logger.exception("chan backtest tool failed")
            return f"Error: 缠论回测失败：{exc}"
        return chan_backtest.format_result(result)

    return StructuredTool.from_function(
        name="chan_backtest",
        description=(
            "回测缠论买卖点的交易计划。因果前缀回放：信号在收盘确认，下一根开盘进场；"
            "一次只持有一笔，固定止损、R 倍目标、最大持仓 K 线。参数：symbol、"
            "granularity（15min/1h/4h/1day/1week）、bars（60-500，默认 300）、"
            "stop_pct（止损百分比，默认 1）、risk_pct（单笔风险百分比，默认 1）、"
            "r_multiple（默认 2）、max_hold_bars（默认 50）、fee_pct/slippage_pct。"
            "返回收益、回撤、胜率、平均 R、按信号类型统计和最近交易；"
            "样本少或没有交易时会明确提示。不是收益承诺。"
        ),
        coroutine=chan_backtest_tool,
    )


def register_chan_tools(
    registry: SkillToolRegistry = SKILL_TOOL_REGISTRY,
) -> None:
    specs = (
        ("chan_analyze", _make_analyze_tool, "分析 crypto 的缠论结构（程序化老笔近似）"),
        ("chan_backtest", _make_backtest_tool, "回测缠论买卖点的交易计划"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_chan_tools()
