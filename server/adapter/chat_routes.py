from server.domain.message_quote import quote_content
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.run_service import RunNotFoundError, SessionRunConflictError
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
    client_message_id: str = Field(default="", max_length=128)
    reply_excerpt: str | None = Field(default=None, min_length=1, max_length=100000)
    reply_to_seq: int | None = Field(default=None, ge=1)
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
        active_key = session_service.build_session_id(
            channel=WEB_CHAT_CHANNEL,
            channel_account_id=WEB_CHAT_ACCOUNT,
            peer_type=WEB_CHAT_PEER_TYPE,
            peer_id=WEB_CHAT_PEER_ID,
            agent_id=resolved_agent_id,
        )
        sessions = session_service.summaries_for_agent(
            agent_id=resolved_agent_id,
            selected_active_key=active_key,
        )
        return {"sessions": sessions}

    @router.post("/session/change")
    def change_chat_session(payload: ChatSessionChangeRequest) -> dict[str, object]:
        session_service = SessionService(container.workspace)
        agent_id = _resolve_agent_id(container.workspace, payload.agent_id)
        try:
            session = session_service.select_active_for_agent(
                channel=WEB_CHAT_CHANNEL,
                channel_account_id=WEB_CHAT_ACCOUNT,
                peer_type=WEB_CHAT_PEER_TYPE,
                peer_id=WEB_CHAT_PEER_ID,
                agent_id=agent_id,
                selector=payload.selector,
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
    def get_chat_messages(session_id: str, agent_id: str = "") -> dict[str, object]:
        session_service = SessionService(container.workspace)
        resolved_agent_id = _resolve_agent_id(container.workspace, agent_id)
        _require_session_for_agent(session_service, session_id, resolved_agent_id)
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
        else:
            _require_session_for_agent(session_service, session_id, agent_id)
        client_message_id = payload.client_message_id.strip()
        existing_run = _find_existing_chat_run(
            container,
            session_id=session_id,
            agent_id=agent_id,
            client_message_id=client_message_id,
        )
        if existing_run is not None:
            return {
                "session": session_service.session_summary(session_id),
                "run": existing_run,
                "deduplicated": True,
            }
        if payload.reply_excerpt is not None and payload.reply_to_seq is None:
            raise HTTPException(400, "引用片段需要来源消息")
        reply = None
        if payload.reply_to_seq is not None:
            target = next((m for m in session_service.read_messages(session_id, limit=1000000)
                           if m.get("seq") == payload.reply_to_seq and m.get("role") in {"user", "assistant"} and m.get("content")), None)
            if target is None:
                raise HTTPException(404, "引用消息不存在于当前会话")
            try:
                quoted = quote_content(target["content"], payload.reply_excerpt)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            reply = {"seq": target["seq"], "session_id": session_id, "speaker": "你" if target["role"] == "user" else "助手", "content": quoted}
        try:
            run = await container.run_service.create_run(
                content=payload.content,
                agent_id=agent_id,
                source="web_chat",
                session_id=session_id,
                attachments=tuple(payload.attachments),
                metadata={
                    "source": "web_chat",
                    **({"reply": reply} if reply else {}),
                    **({"client_message_id": client_message_id} if client_message_id else {}),
                },
            )
        except SessionRunConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "active_run_id": exc.run_id, "status": exc.status},
            ) from exc
        except (ValueError, AgentConfigError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if container.run_worker_service is not None:
            container.run_worker_service.wake()
        return {
            "session": session_service.session_summary(session_id),
            "run": run,
            "deduplicated": False,
        }

    return router


def _find_existing_chat_run(
    container: AppContainer,
    *,
    session_id: str,
    agent_id: str,
    client_message_id: str,
) -> dict[str, object] | None:
    if not client_message_id:
        return None
    for summary in container.run_service.list_runs():
        if (
            summary.get("source") != "web_chat"
            or summary.get("session_id") != session_id
            or summary.get("agent_id") != agent_id
            or summary.get("client_message_id") != client_message_id
        ):
            continue
        run_id = str(summary.get("run_id") or "").strip()
        if not run_id:
            continue
        try:
            run = container.run_service.get_run(run_id)
        except RunNotFoundError:
            continue
        return run
    return None


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


def _require_session_for_agent(session_service: SessionService, session_id: str, agent_id: str) -> dict[str, object]:
    summary = session_service.session_summary(session_id)
    if not summary or summary.get("channel") == "chat_group":
        raise HTTPException(status_code=404, detail="session not found")
    if str(summary.get("agent_id") or "").strip() != agent_id:
        raise HTTPException(status_code=404, detail="session not found for current agent")
    return summary


def _active_session_run(container: AppContainer, session: dict[str, object]) -> dict[str, object] | None:
    run_id = str(session.get("last_run_id") or "").strip()
    if not run_id:
        return None
    try:
        run = container.run_service.get_run(run_id)
    except RunNotFoundError:
        return None
    status = str((run.get("state") or {}).get("status") or "")
    if status not in {"queued", "running", "waiting_approval"}:
        return None
    return run
