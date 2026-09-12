from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.run_service import RunNotFoundError, RunStateError
from server.domain.agent_config import AgentConfigError


class CreateRunRequest(BaseModel):
    content: str
    attachments: list[dict[str, object]] = Field(default_factory=list)
    agent_id: str = ""
    context_ids: list[str] = Field(default_factory=list)
    source: str = "api"
    session_id: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class ResumeRunRequest(BaseModel):
    decision: Literal["approve", "reject"]
    message: str = ""


class RejectRunRequest(BaseModel):
    message: str = ""


def create_run_router(container: AppContainer) -> APIRouter:
    def require_run_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/runs",
        tags=["runs"],
        dependencies=[Depends(require_run_auth)],
    )

    async def resume_waiting_run(
        run_id: str,
        *,
        decision: Literal["approve", "reject"],
        message: str = "",
    ) -> dict[str, object]:
        try:
            resumed = container.run_service.resume_run(run_id, decision=decision, message=message)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except RunStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if container.run_worker_service is not None:
            container.run_worker_service.wake()
        if container.run_delivery_service is not None:
            container.run_delivery_service.wake()
        return resumed

    @router.post("")
    async def create_run(payload: CreateRunRequest) -> dict[str, object]:
        try:
            run = await container.run_service.create_run(
                content=payload.content,
                agent_id=payload.agent_id,
                context_ids=tuple(payload.context_ids),
                source=payload.source,
                session_id=payload.session_id,
                attachments=tuple(payload.attachments),
                metadata=payload.metadata,
            )
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if container.run_worker_service is not None:
            container.run_worker_service.wake()
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

    @router.post("/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, object]:
        try:
            if container.run_worker_service is not None:
                return await container.run_worker_service.cancel_run(run_id)
            return container.run_service.cancel_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @router.post("/{run_id}/rerun")
    async def rerun(run_id: str) -> dict[str, object]:
        try:
            if container.run_service.get_run(run_id).get("input", {}).get("snapshot", {}).get("group_context"):
                raise RunStateError("群聊任务请在群聊中重试当前步骤或继续协作")
            rerun_payload = container.run_service.rerun(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except RunStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if container.run_worker_service is not None:
            container.run_worker_service.wake()
        return rerun_payload

    @router.post("/{run_id}/approve")
    async def approve_run(run_id: str) -> dict[str, object]:
        return await resume_waiting_run(run_id, decision="approve")

    @router.post("/{run_id}/reject")
    async def reject_run(run_id: str, payload: RejectRunRequest | None = None) -> dict[str, object]:
        return await resume_waiting_run(run_id, decision="reject", message=payload.message if payload else "")

    @router.post("/{run_id}/resume")
    async def resume_run(run_id: str, payload: ResumeRunRequest) -> dict[str, object]:
        return await resume_waiting_run(run_id, decision=payload.decision, message=payload.message)

    return router
