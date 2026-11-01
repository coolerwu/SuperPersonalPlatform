from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_auth
from app.config import settings
from app.schemas import MarketProbeOut, MarketStatusOut
from app.services.market_data import market_service

router = APIRouter(
    prefix="/api/market", tags=["market"], dependencies=[Depends(require_auth)]
)


def _proxy_configured() -> bool:
    return bool(
        settings.http_proxy
        or settings.https_proxy
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
    )


@router.get("/candles/status", response_model=MarketStatusOut)
async def candles_status(
    symbol: str = Query(min_length=3, max_length=30),
    granularity: str = Query(min_length=2, max_length=10),
) -> MarketStatusOut:
    try:
        status = await market_service.status(symbol, granularity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MarketStatusOut(
        symbol=status.symbol,
        granularity=status.granularity,
        rows=status.rows,
        first_ms=status.first_ms,
        last_ms=status.last_ms,
        oldest_available_ms=status.oldest_available_ms,
        last_synced_at=status.last_synced_at,
        last_error=status.last_error,
    )


@router.get("/probe", response_model=MarketProbeOut)
async def probe(
    symbol: str = Query(min_length=3, max_length=30),
    granularity: str = Query(min_length=2, max_length=10),
) -> MarketProbeOut:
    try:
        ok, latency, error = await market_service.probe(symbol, granularity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MarketProbeOut(
        ok=ok,
        latency_ms=latency,
        error=error,
        base_url=settings.bitget_base_url,
        proxy_configured=_proxy_configured(),
    )
