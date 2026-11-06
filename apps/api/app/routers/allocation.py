from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import require_auth
from app.schemas import (
    AllocationEntryCreate,
    AllocationEntryUpdate,
    AllocationPortfolioCreate,
    AllocationPortfolioUpdate,
)
from app.services import allocation

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/allocation",
    tags=["allocation"],
    dependencies=[Depends(require_auth)],
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (allocation.PortfolioNotFound, allocation.EntryNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, allocation.AllocationConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, allocation.AllocationError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.exception("allocation request failed")
    return HTTPException(status_code=500, detail="资产配置操作失败")


@router.get("")
async def get_allocation() -> dict:
    try:
        return await allocation.get_snapshot()
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.post("/refresh")
async def refresh_prices() -> dict:
    try:
        return await allocation.refresh_snapshot()
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.post("/portfolios", status_code=201)
async def create_portfolio(payload: AllocationPortfolioCreate) -> dict:
    try:
        return await allocation.create_portfolio(payload.model_dump())
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.put("/portfolios/{portfolio_id}")
async def update_portfolio(
    portfolio_id: str, payload: AllocationPortfolioUpdate
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="没有要修改的字段")
    try:
        return await allocation.update_portfolio(portfolio_id, changes)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.delete("/portfolios/{portfolio_id}", status_code=204)
async def delete_portfolio(portfolio_id: str) -> Response:
    try:
        await allocation.delete_portfolio(portfolio_id)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc
    return Response(status_code=204)


@router.get("/portfolios/{portfolio_id}/entries")
async def list_entries(
    portfolio_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> dict:
    try:
        return await allocation.list_entries(
            portfolio_id, limit=limit, offset=offset
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.post("/portfolios/{portfolio_id}/entries", status_code=201)
async def create_entry(portfolio_id: str, payload: AllocationEntryCreate) -> dict:
    try:
        return await allocation.create_entry(
            portfolio_id, payload.model_dump(exclude_unset=True)
        )
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.put("/portfolios/{portfolio_id}/entries/{entry_id}")
async def update_entry(
    portfolio_id: str, entry_id: str, payload: AllocationEntryUpdate
) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="没有要修改的字段")
    try:
        return await allocation.update_entry(portfolio_id, entry_id, changes)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc


@router.delete("/portfolios/{portfolio_id}/entries/{entry_id}", status_code=204)
async def delete_entry(portfolio_id: str, entry_id: str) -> Response:
    try:
        await allocation.delete_entry(portfolio_id, entry_id)
    except Exception as exc:  # noqa: BLE001 - converted to an HTTP error
        raise _http_error(exc) from exc
    return Response(status_code=204)
