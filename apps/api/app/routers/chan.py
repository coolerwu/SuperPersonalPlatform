from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.schemas import ChanAnalyzeRequest, ChanBacktestRequest
from app.services import chan_analysis, chan_backtest

router = APIRouter(
    prefix="/api/chan",
    tags=["chan"],
    dependencies=[Depends(require_auth)],
)


@router.post("/analyze")
async def analyze(payload: ChanAnalyzeRequest) -> dict:
    try:
        return await chan_analysis.analyze_symbol(
            payload.symbol,
            payload.granularity,
            bars=payload.bars,
        )
    except chan_analysis.ChanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/backtest")
async def backtest(payload: ChanBacktestRequest) -> dict:
    try:
        return await chan_backtest.run_backtest(
            payload.symbol,
            payload.granularity,
            bars=payload.bars,
            initial_capital=payload.initial_capital,
            fee_rate=payload.fee_rate,
            slippage_rate=payload.slippage_rate,
            stop_pct=payload.stop_pct,
            risk_pct=payload.risk_pct,
            r_multiple=payload.r_multiple,
            max_hold_bars=payload.max_hold_bars,
        )
    except chan_backtest.ChanBacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
