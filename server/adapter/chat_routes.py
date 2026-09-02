import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.run_service import RunNotFoundError
from server.app.session_service import SessionService
from server.domain.agent_config import AgentConfigError
from server.infrastructure.config import load_settings


WEB_CHAT_CHANNEL = "web"
WEB_CHAT_ACCOUNT = "default"
WEB_CHAT_PEER_TYPE = "private"
WEB_CHAT_PEER_ID = "browser"


class ChatSessionRequest(BaseModel):
    agent_id: str = ""


class ChatSessionChangeRequest(BaseModel):
    agent_id: str = ""
    selector: str


class ChatMessageRequest(BaseModel):
    content: str
    agent_id: str = ""
    session_id: str = ""
    attachments: list[dict[str, object]] = Field(default_factory=list)


def create_chat_router(container: AppContainer) -> APIRouter:
    def require_chat_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/chat",
        tags=["chat"],
        dependencies=[Depends(require_chat_auth)],
    )

    @router.post("/session")
    def get_chat_session(payload: ChatSessionRequest) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        agent_id = _resolve_agent_id(container.workspace, payload.agent_id)
        session = session_service.get_or_create(
            channel=WEB_CHAT_CHANNEL,
            channel_account_id=WEB_CHAT_ACCOUNT,
            peer_type=WEB_CHAT_PEER_TYPE,
            peer_id=WEB_CHAT_PEER_ID,
            agent_id=agent_id,
            metadata={"source": "web_chat"},
        )
        session_summary = session_service.session_summary(session.session_id)
        return {
            "session": session_summary,
            "messages": session_service.read_messages(session.session_id, limit=80),
            "active_run": _active_session_run(container, session_summary),
        }

    @router.get("/sessions")
    def list_chat_sessions(agent_id: str = "") -> dict[str, object]:
        session_service = SessionService(container.workspace)
        resolved_agent_id = _resolve_agent_id(container.workspace, agent_id)
        sessions = session_service.related_summaries_for_identity(
            channel=WEB_CHAT_CHANNEL,
            channel_account_id=WEB_CHAT_ACCOUNT,
            peer_type=WEB_CHAT_PEER_TYPE,
            peer_id=WEB_CHAT_PEER_ID,
            agent_id=resolved_agent_id,
            limit=30,
        )
        return {"sessions": sessions}

    @router.post("/session/change")
    def change_chat_session(payload: ChatSessionChangeRequest) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        agent_id = _resolve_agent_id(container.workspace, payload.agent_id)
        try:
            session = session_service.switch_active(
                channel=WEB_CHAT_CHANNEL,
                channel_account_id=WEB_CHAT_ACCOUNT,
                peer_type=WEB_CHAT_PEER_TYPE,
                peer_id=WEB_CHAT_PEER_ID,
                agent_id=agent_id,
                selector=payload.selector,
                metadata={"source": "web_chat"},
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "session": session,
            "messages": session_service.read_messages(str(session.get("session_id") or ""), limit=80),
            "active_run": _active_session_run(container, session),
        }

    @router.post("/session/new")
    def new_chat_session(payload: ChatSessionRequest) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        agent_id = _resolve_agent_id(container.workspace, payload.agent_id)
        session = session_service.clear_active(
            channel=WEB_CHAT_CHANNEL,
            channel_account_id=WEB_CHAT_ACCOUNT,
            peer_type=WEB_CHAT_PEER_TYPE,
            peer_id=WEB_CHAT_PEER_ID,
            agent_id=agent_id,
            reason="web chat new session",
            metadata={"source": "web_chat"},
        )
        return {"session": session_service.session_summary(session.session_id), "messages": [], "active_run": None}

    @router.get("/sessions/{session_id}/messages")
    def get_chat_messages(session_id: str) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        if not session_service.exists(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {"messages": session_service.read_messages(session_id, limit=120)}

    @router.post("/messages")
    async def create_chat_message(payload: ChatMessageRequest) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        agent_id = _resolve_agent_id(container.workspace, payload.agent_id)
        session_id = payload.session_id.strip()
        if not session_id:
            session = session_service.get_or_create(
                channel=WEB_CHAT_CHANNEL,
                channel_account_id=WEB_CHAT_ACCOUNT,
                peer_type=WEB_CHAT_PEER_TYPE,
                peer_id=WEB_CHAT_PEER_ID,
                agent_id=agent_id,
                metadata={"source": "web_chat"},
            )
            session_id = session.session_id
        try:
            run = await container.run_service.create_run(
                content=payload.content,
                agent_id=agent_id,
                source="web_chat",
                session_id=session_id,
                attachments=tuple(payload.attachments),
                metadata={"source": "web_chat"},
            )
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        asyncio.create_task(_execute_background(container, str(run["run_id"])))
        return {
            "session": session_service.session_summary(session_id),
            "run": run,
        }

    return router


async def _execute_background(container: AppContainer, run_id: str) -> None:
    try:
        await container.run_service.execute_run(run_id)
    except Exception as exc:
        container.system_log_service.append_line(f"chat run {run_id} failed: {exc}")


def _resolve_agent_id(workspace, raw_agent_id: str) -> str:
    settings = load_settings(workspace / "config.yaml")
    requested = raw_agent_id.strip()
    agents = settings.agent_workspace.agents
    if requested:
        if any(agent.id == requested for agent in agents):
            return requested
        raise HTTPException(status_code=400, detail="Agent does not exist")
    if not agents:
        raise HTTPException(status_code=400, detail="no agents configured")
    return agents[0].id


def _active_session_run(container: AppContainer, session: dict[str, object]) -> dict[str, object] | None:
    run_id = str(session.get("last_run_id") or "").strip()
    if not run_id:
        return None
    try:
        run = container.run_service.get_run(run_id)
    except RunNotFoundError:
        return None
    status = str((run.get("state") or {}).get("status") or "")
    if status not in {"queued", "running"}:
        return None
    return run
