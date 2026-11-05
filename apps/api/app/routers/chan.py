from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.schemas import ChanAnalyzeRequest
from app.services import chan_analysis

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
