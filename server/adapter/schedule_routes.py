from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.schedule_service import ScheduleNotFoundError
from server.domain.agent_config import AgentConfigError


class ScheduleTriggerRequest(BaseModel):
    kind: str = "interval"
    seconds: int = 3600
    expr: str = ""
    timezone: str = "Asia/Shanghai"


class ScheduleRequest(BaseModel):
    id: str
    name: str = ""
    enabled: bool = True
    trigger: ScheduleTriggerRequest = Field(default_factory=ScheduleTriggerRequest)
    agent_id: str
    prompt: str
    context_ids: list[str] = Field(default_factory=list)
    session_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def create_schedule_router(container: AppContainer) -> APIRouter:
    def require_schedule_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/schedules",
        tags=["schedules"],
        dependencies=[Depends(require_schedule_auth)],
    )

    @router.get("")
    def list_schedules() -> dict[str, object]:
        return {"schedules": container.schedule_service.list_schedules()}

    @router.post("")
    def create_schedule(payload: ScheduleRequest) -> dict[str, object]:
        try:
            return container.schedule_service.create_schedule(_payload_dict(payload))
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.get("/{schedule_id}")
    def get_schedule(schedule_id: str) -> dict[str, object]:
        try:
            return container.schedule_service.get_schedule(schedule_id)
        except ScheduleNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found") from exc

    @router.put("/{schedule_id}")
    def update_schedule(schedule_id: str, payload: ScheduleRequest) -> dict[str, object]:
        try:
            return container.schedule_service.update_schedule(schedule_id, _payload_dict(payload))
        except ScheduleNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found") from exc
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.delete("/{schedule_id}")
    def delete_schedule(schedule_id: str) -> dict[str, object]:
        try:
            container.schedule_service.delete_schedule(schedule_id)
        except ScheduleNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"ok": True}

    @router.post("/{schedule_id}/run-now")
    async def run_schedule_now(schedule_id: str) -> dict[str, object]:
        try:
            return await container.schedule_service.run_now(schedule_id)
        except ScheduleNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="schedule not found") from exc
        except (ValueError, RuntimeError, AgentConfigError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return router


def _payload_dict(payload: ScheduleRequest) -> dict[str, Any]:
    return payload.model_dump()
