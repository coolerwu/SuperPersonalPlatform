from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated


def create_channel_router(container: AppContainer) -> APIRouter:
    def require_channel_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/channels",
        tags=["channels"],
        dependencies=[Depends(require_channel_auth)],
    )

    def wechat_service():
        if container.wechat_channel_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="wechat channel service is unavailable",
            )
        return container.wechat_channel_service

    def serialize_status(value) -> dict[str, object]:
        return asdict(value) if is_dataclass(value) else dict(value)

    @router.get("/wechat/status")
    async def wechat_status() -> dict[str, object]:
        return {"wechat": serialize_status(await wechat_service().status())}

    @router.post("/wechat/start")
    async def wechat_start() -> dict[str, object]:
        return {"wechat": serialize_status(await wechat_service().start())}

    @router.post("/wechat/stop")
    async def wechat_stop() -> dict[str, object]:
        return {"wechat": serialize_status(await wechat_service().stop())}

    return router
