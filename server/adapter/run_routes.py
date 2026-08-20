import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.run_service import RunNotFoundError
from server.domain.agents import AgentConfigError


class CreateRunRequest(BaseModel):
    content: str
    agent_id: str = ""
    context_ids: list[str] = Field(default_factory=list)
    source: str = "api"
    metadata: dict[str, object] = Field(default_factory=dict)


def create_run_router(container: AppContainer) -> APIRouter:
    def require_run_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/runs",
        tags=["runs"],
        dependencies=[Depends(require_run_auth)],
    )

    @router.post("")
    async def create_run(payload: CreateRunRequest) -> dict[str, object]:
        try:
            run = await container.run_service.create_run(
                content=payload.content,
                agent_id=payload.agent_id,
                context_ids=tuple(payload.context_ids),
                source=payload.source,
                metadata=payload.metadata,
            )
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        asyncio.create_task(_execute_background(container, str(run["run_id"])))
        return run

    @router.get("")
    def list_runs() -> dict[str, object]:
        return {"runs": container.run_service.list_runs()}

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return container.run_service.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @router.get("/{run_id}/events")
    def get_run_events(run_id: str, after: int = 0) -> dict[str, object]:
        try:
            return {"events": container.run_service.get_events(run_id, after=after)}
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    return router


async def _execute_background(container: AppContainer, run_id: str) -> None:
    try:
        await container.run_service.execute_run(run_id)
    except Exception as exc:
        container.system_log_service.append_line(f"run {run_id} failed: {exc}")
