from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated


class CreateSelfDevTaskPayload(BaseModel):
    goal: str
    agent_id: str
    repo_url: str | None = None


class AcceptSelfDevTaskPayload(BaseModel):
    note: str | None = None


class RunSelfDevTaskPayload(BaseModel):
    instruction: str | None = None


class RejectSelfDevTaskPayload(BaseModel):
    reason: str | None = None


def create_self_dev_router(container: AppContainer) -> APIRouter:
    def require_self_dev_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/self-dev",
        tags=["self-dev"],
        dependencies=[Depends(require_self_dev_auth)],
    )

    def service():
        if container.self_dev_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="self-dev service is unavailable",
            )
        return container.self_dev_service

    @router.get("/tasks")
    def list_tasks() -> dict[str, object]:
        return {"tasks": [task.__dict__ for task in service().list_tasks()]}

    @router.post("/tasks")
    def create_task(payload: CreateSelfDevTaskPayload) -> dict[str, object]:
        if not payload.goal.strip():
            raise HTTPException(status_code=400, detail="goal is required")
        if not payload.agent_id.strip():
            raise HTTPException(status_code=400, detail="agent_id is required")
        task = service().create_task(payload.goal, payload.agent_id, payload.repo_url or "")
        return {"task": task.__dict__}

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, object]:
        try:
            task_data = service().get_task(task_id)
            task_data["is_running"] = service().is_task_running(task_id)
            return {"task": task_data}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @router.post("/tasks/{task_id}/run")
    async def run_task(task_id: str, payload: RunSelfDevTaskPayload | None = None) -> dict[str, object]:
        task = await service().run_task(task_id, instruction=(payload.instruction if payload else "") or "")
        return {"task": task.__dict__}

    @router.post("/tasks/{task_id}/accept")
    async def accept_task(task_id: str, payload: AcceptSelfDevTaskPayload) -> dict[str, object]:
        try:
            task = await service().accept_task(task_id, payload.note or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task.__dict__}

    @router.post("/tasks/{task_id}/reject")
    async def reject_task(task_id: str, payload: RejectSelfDevTaskPayload) -> dict[str, object]:
        try:
            task = await service().reject_task(task_id, payload.reason or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task.__dict__}

    return router
