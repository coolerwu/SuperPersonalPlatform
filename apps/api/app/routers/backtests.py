from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_auth
from app.config import settings
from app.routers.market import _proxy_configured
from app.schemas import (
    BacktestCreate,
    BacktestDetailOut,
    BacktestGranularityOut,
    BacktestMetaOut,
    BacktestResultOut,
    BacktestSummaryOut,
)
from app.services import backtest_service
from app.services.timeframes import GRANULARITIES, granularity_label

router = APIRouter(
    prefix="/api/backtests", tags=["backtests"], dependencies=[Depends(require_auth)]
)


@router.post("", response_model=BacktestDetailOut, status_code=201)
async def create_backtest(payload: BacktestCreate, request: Request) -> BacktestDetailOut:
    row = await backtest_service.create_backtest(payload)
    worker = getattr(request.app.state, "backtest_worker", None)
    if worker is not None:
        worker.wake()
    return backtest_service.detail_out(row)


@router.get("", response_model=list[BacktestSummaryOut])
async def list_backtests(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> list[BacktestSummaryOut]:
    if status is not None and status not in backtest_service.STATUSES:
        raise HTTPException(status_code=422, detail="未知的回测状态")
    rows = await backtest_service.list_backtests(limit=limit, status=status)
    return [backtest_service.summary_out(row) for row in rows]


@router.get("/meta", response_model=BacktestMetaOut)
async def backtest_meta(request: Request) -> BacktestMetaOut:
    worker = getattr(request.app.state, "backtest_worker", None)
    return BacktestMetaOut(
        granularities=[
            BacktestGranularityOut(value=value, label=granularity_label(value))
            for value in GRANULARITIES
        ],
        max_bars=settings.backtest_max_bars,
        max_trades=settings.backtest_max_trades,
        worker_running=bool(worker is not None and worker.running),
        proxy_configured=_proxy_configured(),
        bitget_base_url=settings.bitget_base_url,
    )


@router.get("/{backtest_id}", response_model=BacktestDetailOut)
async def get_backtest(backtest_id: str) -> BacktestDetailOut:
    row = await backtest_service.get_backtest(backtest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    return backtest_service.detail_out(row)


@router.get("/{backtest_id}/result", response_model=BacktestResultOut)
async def get_backtest_result(backtest_id: str) -> BacktestResultOut:
    row = await backtest_service.get_backtest(backtest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回测不存在")
    if row.status != "completed":
        raise HTTPException(status_code=409, detail="回测尚未完成")
    return BacktestResultOut(
        id=row.id,
        status=row.status,
        metrics=row.metrics,
        equity=row.equity or [],
        drawdown=row.drawdown or [],
        candles=row.candles or [],
        trades=row.trades or [],
        warnings=row.warnings or [],
        trades_total=row.trades_total,
        trades_truncated=row.trades_truncated,
        data_start_ms=row.data_start_ms,
        data_end_ms=row.data_end_ms,
        bar_count=row.bar_count,
    )


@router.post("/{backtest_id}/cancel")
async def cancel_backtest(backtest_id: str, request: Request) -> dict[str, bool]:
    worker = getattr(request.app.state, "backtest_worker", None)
    try:
        if worker is not None:
            cancelled = await worker.cancel(backtest_id)
        else:
            cancelled = await backtest_service.cancel_backtest(backtest_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="回测不存在") from exc
    return {"cancelled": cancelled}
