from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.portfolio import HoldingNotFoundError


# ── Pydantic request/response models ──────────────────────────────────

class CreateHoldingPayload(BaseModel):
    type: str
    symbol: str
    name: str = ""
    quantity: float
    avg_cost: float
    currency: str = "CNY"
    notes: str = ""


class UpdateHoldingPayload(BaseModel):
    type: str | None = None
    symbol: str | None = None
    name: str | None = None
    quantity: float | None = None
    avg_cost: float | None = None
    currency: str | None = None
    notes: str | None = None


def _holding_to_response(h) -> dict:
    return {
        "id": h.id,
        "type": h.type,
        "symbol": h.symbol,
        "name": h.name,
        "quantity": h.quantity,
        "avg_cost": h.avg_cost,
        "total_cost": h.total_cost,
        "currency": h.currency,
        "notes": h.notes,
        "created_at": h.created_at,
        "updated_at": h.updated_at,
    }


# ── Router factory ────────────────────────────────────────────────────

def create_portfolio_router(container: AppContainer) -> APIRouter:
    def require_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])
    service = container.portfolio_service
    if service is None:
        return router

    # ── CRUD: holdings ────────────────────────────────────────────────

    @router.get("/holdings", dependencies=[Depends(require_auth)])
    def list_holdings() -> dict:
        return {"holdings": [_holding_to_response(h) for h in service.list_holdings()]}

    @router.post("/holdings", dependencies=[Depends(require_auth)], status_code=status.HTTP_201_CREATED)
    def create_holding(payload: CreateHoldingPayload) -> dict:
        try:
            h = service.create_holding(
                type_=payload.type,
                symbol=payload.symbol,
                name=payload.name,
                quantity=payload.quantity,
                avg_cost=payload.avg_cost,
                currency=payload.currency,
                notes=payload.notes,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.get("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def get_holding(holding_id: str) -> dict:
        try:
            h = service.get_holding(holding_id)
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.put("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def update_holding(holding_id: str, payload: UpdateHoldingPayload) -> dict:
        try:
            h = service.update_holding(
                holding_id,
                type_=payload.type,
                symbol=payload.symbol,
                name=payload.name,
                quantity=payload.quantity,
                avg_cost=payload.avg_cost,
                currency=payload.currency,
                notes=payload.notes,
            )
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.delete("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def delete_holding(holding_id: str) -> dict:
        try:
            service.delete_holding(holding_id)
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return {"ok": True}

    return router
