from fastapi import APIRouter, HTTPException, Request, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.errors import UpstreamLogsError


def create_proxy_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/proxy", tags=["proxy"])

    @router.get("/logs")
    async def logs(request: Request) -> dict[str, object]:
        require_authenticated(request, container)
        try:
            payload = await container.logs_service.get_logs()
        except UpstreamLogsError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to read upstream logs",
            ) from exc
        return {"type": payload.type, "data": payload.data}

    return router
