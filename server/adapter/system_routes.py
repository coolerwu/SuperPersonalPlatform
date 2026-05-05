from fastapi import APIRouter, HTTPException, Request, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.system_update_service import UpdateAlreadyRunningError


def create_system_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system"])

    @router.post("/update-service")
    def update_service(request: Request) -> dict[str, str | bool]:
        require_authenticated(request, container)
        try:
            log_path = container.system_update_service.start_update()
        except UpdateAlreadyRunningError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="更新任务已经在执行",
            ) from exc

        return {
            "ok": True,
            "message": "更新已开始",
            "log_path": str(log_path),
        }

    return router
