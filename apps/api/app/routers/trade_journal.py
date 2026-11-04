from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_auth
from app.schemas import TradeJournalClose, TradeJournalCreate, TradeJournalUpdate
from app.services import trade_journal

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/trade-journal",
    tags=["trade-journal"],
    dependencies=[Depends(require_auth)],
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, trade_journal.TradeJournalNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, trade_journal.TradeJournalError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.exception("trade journal request failed")
    return HTTPException(status_code=500, detail="交易记录操作失败")


@router.get("")
async def list_records(
    market: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    search: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> dict:
    try:
        return await trade_journal.query_records(
            market=market,
            symbol=symbol,
            direction=direction,
            status=status,
            tag=tag,
            start=start,
            end=end,
            search=search,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.get("/stats")
async def record_stats(
    market: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict:
    try:
        return await trade_journal.stats(
            market=market,
            symbol=symbol,
            direction=direction,
            tag=tag,
            start=start,
            end=end,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.post("", status_code=201)
async def create_record(payload: TradeJournalCreate) -> dict:
    try:
        return await trade_journal.create_record(
            **payload.model_dump(),
            source="manual",
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.get("/{record_id}")
async def get_record(record_id: str) -> dict:
    try:
        result = await trade_journal.query_records(record_id=record_id)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc
    items = result.get("items") or []
    if not items:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    return items[0]


@router.put("/{record_id}")
async def update_record(record_id: str, payload: TradeJournalUpdate) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="没有要修改的字段")
    try:
        return await trade_journal.update_record(record_id, **changes)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.post("/{record_id}/close")
async def close_record(record_id: str, payload: TradeJournalClose) -> dict:
    try:
        return await trade_journal.close_record(
            record_id,
            **payload.model_dump(exclude_unset=True),
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc
