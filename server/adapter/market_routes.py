from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.market_service import MarketRunConflictError, MarketServiceError


class WatchSymbolRequest(BaseModel):
    symbol: str
    alias: str = ""


class AnalyzeRequest(BaseModel):
    symbol: str


def create_market_router(container: AppContainer) -> APIRouter:
    def require_market_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/market",
        tags=["market"],
        dependencies=[Depends(require_market_auth)],
    )

    def service():
        market_service = container.market_service
        if market_service is None:
            raise HTTPException(status_code=503, detail="market service is unavailable")
        return market_service

    @router.get("/overview")
    def overview() -> dict[str, object]:
        market_service = service()
        snapshots = market_service.all_snapshots()
        return {
            "enabled": market_service.config.enabled,
            "exchange": market_service.config.exchange,
            "symbols": [_overview_item(item) for item in snapshots],
            "events": market_service.events_summary(),
        }

    @router.get("/snapshot")
    def snapshot(symbol: str) -> dict[str, object]:
        market_service = service()
        found = market_service.snapshot(symbol)
        if found is None:
            raise HTTPException(status_code=404, detail="no snapshot for this symbol yet")
        return {
            "snapshot": found.to_dict(),
            "events": market_service.events_summary(),
        }

    @router.get("/bars")
    def bars(symbol: str, timeframe: str, limit: int = 300) -> dict[str, object]:
        market_service = service()
        return {"bars": market_service.load_bars(symbol, timeframe, limit)}

    @router.get("/events")
    def events(after: int = 0, symbol: str = "", limit: int = 100) -> dict[str, object]:
        market_service = service()
        return {
            "events": [item.to_dict() for item in market_service.list_events(after_seq=after, symbol=symbol, limit=limit)],
            "summary": market_service.events_summary(),
        }

    @router.post("/events/read")
    def mark_events_read() -> dict[str, object]:
        return service().mark_events_read()

    @router.get("/symbols")
    def list_symbols() -> dict[str, object]:
        return {"symbols": [{"symbol": item.symbol, "alias": item.alias} for item in service().watchlist()]}

    @router.post("/symbols")
    def add_symbol(payload: WatchSymbolRequest) -> dict[str, object]:
        try:
            entry = service().add_symbol(payload.symbol, payload.alias)
        except MarketServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"symbol": entry.symbol, "alias": entry.alias}

    @router.delete("/symbols")
    def remove_symbol(symbol: str) -> dict[str, object]:
        # 交易对写法里带斜杠，做不了路径参数，和其余接口一样走查询参数。
        try:
            service().remove_symbol(symbol)
        except MarketServiceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"removed": symbol}

    @router.get("/commentary")
    def commentary(symbol: str) -> dict[str, object]:
        market_service = service()
        return {
            "symbol": symbol,
            "commentary": market_service.list_commentary(symbol),
            "active_run": market_service.active_commentary_run(symbol),
            "session_id": market_service.commentary_session_id(symbol),
        }

    @router.post("/analyze")
    async def analyze(payload: AnalyzeRequest) -> dict[str, object]:
        try:
            return await service().analyze(payload.symbol)
        except MarketRunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MarketServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/refresh")
    async def refresh() -> dict[str, object]:
        market_service = service()
        await market_service.run_cycle()
        return {"symbols": [item.symbol for item in market_service.all_snapshots()]}

    return router


def _overview_item(snapshot) -> dict[str, object]:
    """左栏只需要报价和一段方向条，不返回整份指标。"""
    return {
        "symbol": snapshot.symbol,
        "alias": snapshot.alias,
        "last_price": snapshot.last_price,
        "change_pct_24h": snapshot.change_pct_24h,
        "stale": snapshot.stale,
        "data_age_seconds": snapshot.data_age_seconds,
        "fast_signals": sum(len(frame.signals) for frame in snapshot.timeframes if frame.fast),
        "timeframes": [
            {
                "timeframe": frame.timeframe,
                "fast": frame.fast,
                "trend": frame.trend,
                "change_pct": frame.change_pct,
            }
            for frame in snapshot.timeframes
        ],
    }
