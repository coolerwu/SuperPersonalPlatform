from __future__ import annotations

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.orm import defer

from app.db import SessionLocal
from app.models import Backtest, utcnow
from app.schemas import BacktestCreate, BacktestDetailOut, BacktestSummaryOut

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "fetching", "running")
RUNNING_STATUSES = ("fetching", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
STATUSES = ("queued", "fetching", "running", "completed", "failed", "cancelled")


async def create_backtest(payload: BacktestCreate) -> Backtest:
    async with SessionLocal() as db:
        row = Backtest(
            symbol=payload.symbol,
            granularity=payload.granularity,
            start_ms=payload.start_ms,
            end_ms=payload.end_ms,
            initial_capital=payload.initial_capital,
            fee_rate=payload.fee_rate,
            slippage_rate=payload.slippage_rate,
            strategy_code=payload.strategy_code,
            status="queued",
            progress={"stage": "queued"},
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def list_backtests(limit: int = 50, status: str | None = None) -> list[Backtest]:
    async with SessionLocal() as db:
        statement = (
            select(Backtest)
            .order_by(Backtest.created_at.desc())
            .limit(limit)
            .options(
                defer(Backtest.equity),
                defer(Backtest.drawdown),
                defer(Backtest.candles),
                defer(Backtest.trades),
            )
        )
        if status:
            statement = statement.where(Backtest.status == status)
        return list((await db.scalars(statement)).all())


async def get_backtest(backtest_id: str) -> Backtest | None:
    async with SessionLocal() as db:
        return await db.get(Backtest, backtest_id)


async def claim_next_backtest() -> str | None:
    async with SessionLocal() as db:
        while True:
            row = await db.scalar(
                select(Backtest)
                .where(Backtest.status == "queued")
                .order_by(Backtest.created_at)
                .limit(1)
            )
            if row is None:
                return None
            if row.cancel_requested:
                row.status = "cancelled"
                row.finished_at = utcnow()
                row.progress = {"stage": "cancelled"}
                await db.commit()
                continue
            row.status = "fetching"
            row.started_at = utcnow()
            row.progress = {"stage": "fetching", "fetched_bars": 0}
            await db.commit()
            return row.id


async def mark_running(backtest_id: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(Backtest, backtest_id)
        if row is not None and row.status == "fetching" and not row.cancel_requested:
            row.status = "running"
            row.progress = {"stage": "running"}
            await db.commit()


async def update_progress(backtest_id: str, progress: dict) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(Backtest)
            .where(
                Backtest.id == backtest_id,
                Backtest.status.in_(RUNNING_STATUSES),
                Backtest.cancel_requested.is_(False),
            )
            .values(progress=progress)
        )
        await db.commit()


async def complete_backtest(
    backtest_id: str,
    *,
    result: dict,
    data_start_ms: int | None,
    data_end_ms: int | None,
    bar_count: int,
    warnings: list[str],
) -> bool:
    metrics = result.get("metrics") or {}
    async with SessionLocal() as db:
        outcome = await db.execute(
            update(Backtest)
            .where(
                Backtest.id == backtest_id,
                Backtest.status.in_(RUNNING_STATUSES),
                Backtest.cancel_requested.is_(False),
            )
            .values(
                status="completed",
                finished_at=utcnow(),
                error=None,
                error_detail=None,
                progress={"stage": "completed"},
                metrics=metrics,
                equity=result.get("equity") or [],
                drawdown=result.get("drawdown") or [],
                candles=result.get("candles") or [],
                trades=result.get("trades") or [],
                warnings=warnings,
                data_start_ms=result.get("metrics", {}).get("start_ms", data_start_ms),
                data_end_ms=result.get("metrics", {}).get("end_ms", data_end_ms),
                bar_count=bar_count,
                trades_total=int(result.get("trades_total") or 0),
                trades_truncated=bool(result.get("trades_truncated")),
            )
        )
        await db.commit()
        if outcome.rowcount:
            return True
    await _cancel_if_active(backtest_id)
    return False


async def fail_backtest(
    backtest_id: str, message: str, detail: str | None = None
) -> bool:
    async with SessionLocal() as db:
        row = await db.get(Backtest, backtest_id)
        if row is None:
            return False
        if row.status in TERMINAL_STATUSES:
            return False
        if row.cancel_requested:
            await _cancel_if_active(backtest_id)
            return False
        row.status = "failed"
        row.error = message
        row.error_detail = detail
        row.finished_at = utcnow()
        row.progress = {"stage": "failed"}
        await db.commit()
        return True


async def cancel_backtest(backtest_id: str) -> bool:
    async with SessionLocal() as db:
        row = await db.get(Backtest, backtest_id)
        if row is None:
            raise LookupError("backtest not found")
        if row.status in TERMINAL_STATUSES:
            return False
        row.cancel_requested = True
        if row.status == "queued":
            row.status = "cancelled"
            row.finished_at = utcnow()
            row.progress = {"stage": "cancelled"}
        await db.commit()
        return True


async def cancel_backtest_record(backtest_id: str) -> None:
    await _cancel_if_active(backtest_id)


async def requeue_backtest(backtest_id: str) -> None:
    async with SessionLocal() as db:
        row = await db.get(Backtest, backtest_id)
        if row is None or row.status in TERMINAL_STATUSES:
            return
        row.status = "queued"
        row.started_at = None
        row.progress = {"stage": "queued", "reason": "service_restart"}
        await db.commit()


async def requeue_backtests() -> None:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.scalars(
                    select(Backtest).where(Backtest.status.in_(RUNNING_STATUSES))
                )
            ).all()
        )
        for row in rows:
            if row.cancel_requested:
                row.status = "cancelled"
                row.finished_at = utcnow()
                row.progress = {"stage": "cancelled"}
            else:
                row.status = "queued"
                row.started_at = None
                row.progress = {"stage": "queued", "reason": "service_restart"}
        await db.commit()


async def delete_all_backtests() -> None:
    """Test helper: remove rows so workers start from a clean queue."""
    async with SessionLocal() as db:
        await db.execute(delete(Backtest))
        await db.commit()


async def _cancel_if_active(backtest_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            update(Backtest)
            .where(Backtest.id == backtest_id, Backtest.status.in_(RUNNING_STATUSES))
            .values(
                status="cancelled",
                finished_at=utcnow(),
                progress={"stage": "cancelled"},
            )
        )
        await db.commit()


def summary_out(row: Backtest) -> BacktestSummaryOut:
    return BacktestSummaryOut.model_validate(row)


def detail_out(row: Backtest) -> BacktestDetailOut:
    return BacktestDetailOut.model_validate(row)
