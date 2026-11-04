"""Agent tools for the personal trade journal."""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, StructuredTool

from app.services import trade_journal
from app.services.skill_tools import (
    SKILL_TOOL_REGISTRY,
    SkillToolContext,
    SkillToolRegistry,
)

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "trade_journal_record",
    "trade_journal_update",
    "trade_journal_close",
    "trade_journal_query",
    "trade_journal_stats",
)


def _error(exc: Exception) -> str:
    if isinstance(exc, trade_journal.TradeJournalError):
        return f"Error: {exc}"
    logger.exception("trade journal tool failed")
    return f"Error: 交易记录操作失败：{exc}"


def _detail(record: dict) -> str:
    text = trade_journal.format_record(record, detail=True)
    if len(text) > trade_journal.MAX_RESULT_CHARS:
        text = text[: trade_journal.MAX_RESULT_CHARS] + "\n... [结果过大已截断]"
    return text


def _make_record_tool(context: SkillToolContext) -> BaseTool:
    async def trade_journal_record(
        symbol: str,
        entry_at: str,
        entry_price: float,
        thesis: str,
        direction: str = "long",
        market: str = "",
        currency: str = "",
        instrument_type: str = "",
        stop_loss: float | None = None,
        quantity: float | None = None,
        fees: float = 0.0,
        exit_at: str = "",
        exit_price: float | None = None,
        review: str = "",
        tags: list[str] | None = None,
        followed_plan: bool | None = None,
        meta: dict | None = None,
    ) -> str:
        """记录一笔交易或交易计划。"""
        try:
            record = await trade_journal.create_record(
                symbol=symbol,
                direction=direction,
                entry_at=entry_at,
                entry_price=entry_price,
                thesis=thesis,
                market=market or None,
                currency=currency or None,
                instrument_type=instrument_type or None,
                stop_loss=stop_loss,
                quantity=quantity,
                fees=fees,
                exit_at=exit_at or None,
                exit_price=exit_price,
                review=review,
                tags=tags,
                followed_plan=followed_plan,
                meta=meta,
                source="agent",
                agent_id=context.agent_id,
            )
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            return _error(exc)
        return "交易记录已写入。\n" + _detail(record)

    return StructuredTool.from_function(
        name="trade_journal_record",
        description=(
            "记录一笔真实、模拟或历史交易。required: symbol、entry_at、entry_price、thesis。"
            "entry_at 使用 ISO 8601（如 2026-11-02T10:15:00+08:00；crypto 可用 Z）。"
            "symbol 例如 BTCUSDT、600519.SH；market 可省略，会自动推断 crypto / a_share。"
            "direction 为 long 或 short。可选：stop_loss、quantity、fees、exit_at、exit_price、"
            "review、tags、followed_plan、meta。已平仓时 exit_at 和 exit_price 必须同时提供。"
            "thesis 是开仓理由，不能编造；用户没给的价格、时间、数量先问，不要猜。"
            "tags 推荐使用 setup:、mistake:、emotion:、regime:、board:、sector:、limit: 前缀。"
        ),
        coroutine=trade_journal_record,
    )


def _make_update_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def trade_journal_update(
        record_id: str,
        symbol: str | None = None,
        direction: str | None = None,
        entry_at: str | None = None,
        entry_price: float | None = None,
        market: str | None = None,
        currency: str | None = None,
        instrument_type: str | None = None,
        stop_loss: float | None = None,
        quantity: float | None = None,
        fees: float | None = None,
        exit_at: str | None = None,
        exit_price: float | None = None,
        thesis: str | None = None,
        review: str | None = None,
        tags: list[str] | None = None,
        followed_plan: bool | None = None,
        meta: dict | None = None,
    ) -> str:
        """修改一笔已有交易记录。"""
        changes: dict = {"record_id": record_id}
        for key, value in (
            ("symbol", symbol),
            ("direction", direction),
            ("entry_at", entry_at),
            ("entry_price", entry_price),
            ("market", market),
            ("currency", currency),
            ("instrument_type", instrument_type),
            ("stop_loss", stop_loss),
            ("quantity", quantity),
            ("fees", fees),
            ("exit_at", exit_at),
            ("exit_price", exit_price),
            ("thesis", thesis),
            ("review", review),
            ("tags", tags),
            ("followed_plan", followed_plan),
            ("meta", meta),
        ):
            if value is not None:
                changes[key] = value
        try:
            record = await trade_journal.update_record(**changes)
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            return _error(exc)
        return "交易记录已更新。\n" + _detail(record)

    return StructuredTool.from_function(
        name="trade_journal_update",
        description=(
            "修改已有交易记录，只传要改的字段。record_id 来自 trade_journal_record 或 "
            "trade_journal_query。平仓优先用 trade_journal_close；修正已平仓记录也可以用它。"
            "不要把用户没说的数据补进去。"
        ),
        coroutine=trade_journal_update,
    )


def _make_close_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def trade_journal_close(
        record_id: str,
        exit_at: str,
        exit_price: float,
        fees: float | None = None,
        review: str | None = None,
        tags: list[str] | None = None,
        followed_plan: bool | None = None,
    ) -> str:
        """平仓并补充复盘。"""
        changes: dict = {
            "record_id": record_id,
            "exit_at": exit_at,
            "exit_price": exit_price,
        }
        for key, value in (
            ("fees", fees),
            ("review", review),
            ("tags", tags),
            ("followed_plan", followed_plan),
        ):
            if value is not None:
                changes[key] = value
        try:
            record = await trade_journal.close_record(**changes)
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            return _error(exc)
        return "交易记录已平仓。\n" + _detail(record)

    return StructuredTool.from_function(
        name="trade_journal_close",
        description=(
            "按 record_id 平仓：exit_at 和 exit_price 必填，可同时补 fees、review、tags、"
            "followed_plan。平仓后平台会计算价格收益、R 和净盈亏；不要手工填这些计算结果。"
        ),
        coroutine=trade_journal_close,
    )


def _make_query_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def trade_journal_query(
        record_id: str = "",
        market: str = "",
        symbol: str = "",
        direction: str = "",
        status: str = "",
        tag: str = "",
        start: str = "",
        end: str = "",
        search: str = "",
        limit: int = 20,
        offset: int = 0,
        detail: bool = False,
    ) -> str:
        """查询交易记录。"""
        try:
            result = await trade_journal.query_records(
                record_id=record_id or None,
                market=market or None,
                symbol=symbol or None,
                direction=direction or None,
                status=status or None,
                tag=tag or None,
                start=start or None,
                end=end or None,
                search=search,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            return _error(exc)
        return trade_journal.format_query(
            result, detail=detail or bool(record_id)
        )

    return StructuredTool.from_function(
        name="trade_journal_query",
        description=(
            "查询交易记录。可按 record_id 取单笔详情，或按 market、symbol、direction、"
            "status（open/closed）、tag、start/end（日期或 ISO 时间；日期形式的 end 含当天，"
            "ISO 时间形式的 end 不含）、search（理由/复盘/标签关键词）筛选。limit 1-50，"
            "offset 从 0 开始；detail=true 返回止损、理由、复盘等完整内容。"
        ),
        coroutine=trade_journal_query,
    )


def _make_stats_tool(context: SkillToolContext) -> BaseTool:
    del context

    async def trade_journal_stats(
        market: str = "",
        symbol: str = "",
        direction: str = "",
        tag: str = "",
        start: str = "",
        end: str = "",
    ) -> str:
        """统计交易表现。"""
        try:
            result = await trade_journal.stats(
                market=market or None,
                symbol=symbol or None,
                direction=direction or None,
                tag=tag or None,
                start=start or None,
                end=end or None,
            )
        except Exception as exc:  # noqa: BLE001 - tools return errors as text
            return _error(exc)
        return trade_journal.format_stats(result)

    return StructuredTool.from_function(
        name="trade_journal_stats",
        description=(
            "统计交易记录：笔数、胜率、平均收益、平均 R、盈亏比、累计盈亏，并按 market 和 "
            "tags 分组。可按 market、symbol、direction、tag、start/end 过滤。"
            "多币种盈亏不会直接相加。"
        ),
        coroutine=trade_journal_stats,
    )


def register_trade_journal_tools(
    registry: SkillToolRegistry = SKILL_TOOL_REGISTRY,
) -> None:
    specs = (
        ("trade_journal_record", _make_record_tool, "记录一笔交易"),
        ("trade_journal_update", _make_update_tool, "修改交易记录"),
        ("trade_journal_close", _make_close_tool, "平仓并补充复盘"),
        ("trade_journal_query", _make_query_tool, "查询交易记录"),
        ("trade_journal_stats", _make_stats_tool, "统计交易表现"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_trade_journal_tools()
