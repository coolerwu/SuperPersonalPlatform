from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.system_log_service import InvalidLogFileError
from server.app.system_update_service import UpdateAlreadyRunningError


class LogReadRequest(BaseModel):
    name: str


def create_system_router(container: AppContainer) -> APIRouter:
    def require_system_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/system",
        tags=["system"],
        dependencies=[Depends(require_system_auth)],
    )

    @router.post("/logs/list")
    def list_logs() -> dict[str, list[dict[str, str | int]]]:
        return {
            "logs": [
                {
                    "name": log.name,
                    "path": log.path,
                    "size": log.size,
                    "modified_at": log.modified_at,
                }
                for log in container.system_log_service.list_logs()
            ]
        }

    @router.post("/logs/read")
    def read_log(payload: LogReadRequest) -> dict[str, str | int | bool]:
        try:
            log = container.system_log_service.read_log(payload.name)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="日志文件不存在",
            ) from exc
        except InvalidLogFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="日志文件名无效",
            ) from exc
        return {
            "name": log.name,
            "path": log.path,
            "size": log.size,
            "modified_at": log.modified_at,
            "content": log.content,
            "truncated": log.truncated,
        }

    @router.post("/update-service")
    def update_service() -> dict[str, str | bool]:
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
