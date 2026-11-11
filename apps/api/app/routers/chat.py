from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import (
    ChatCommandRequest,
    ChatCommandResponse,
    ChatRequest,
    TurnAcceptedOut,
)
from app.services import commands
from app.services.claude_runner import Turn, transcript_path
from app.services.identifiers import is_canonical_uuid, valid_agent_id
from app.services.session_registry import SessionDeletingError
from app.services.settings_store import get_settings_store

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])


def _registry(request: Request):
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    return registry


def _store(request: Request):
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


@router.get("/slash-commands")
async def slash_commands(agent_id: str, request: Request) -> dict[str, list[str]]:
    """Slash commands the Claude Code runtime currently exposes for an Agent.

    The list comes from the CLI's init frame, so the chat palette mirrors what
    the runtime actually supports instead of a hardcoded copy. Empty until the
    Agent has run at least one turn in this process.
    """
    runner = getattr(request.app.state, "runner", None)
    lookup = getattr(runner, "slash_commands", None)
    return {"commands": lookup(agent_id) if lookup is not None else []}


async def _validate_session_owner(
    request: Request, agent_id: str, session_id: str
) -> None:
    """Refuse turns that would run another Agent's session id.

    A session id names a transcript file inside the Agent's project directory
    and the runner's client cache is scoped per Agent. Reusing an id across
    Agents would create a second transcript with the same UUID and could run
    with the wrong workspace/instructions, so the payload must match the
    existing owner whenever one is known.
    """
    settings = await _store(request).get()
    agent = next((item for item in settings.agents if item.id == agent_id), None)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if agent.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Agent 已暂停或归档，无法接收新消息；请先在配置页恢复为 active。",
        )
    if not valid_agent_id(agent_id):
        raise HTTPException(status_code=400, detail="invalid agent id")
    if transcript_path(session_id, agent_id).is_file():
        return
    registry = getattr(request.app.state, "registry", None)
    claimed = None
    if registry is not None:
        claimed = getattr(registry, "session_agent", lambda _sid: None)(session_id)
    if claimed is not None and claimed != agent_id:
        raise HTTPException(
            status_code=409, detail="session id belongs to another agent"
        )
    for other in settings.agents:
        if other.id == agent_id or not valid_agent_id(other.id):
            continue
        if transcript_path(session_id, other.id).is_file():
            raise HTTPException(
                status_code=409, detail="session id belongs to another agent"
            )


@router.post("/messages", response_model=TurnAcceptedOut, status_code=201)
async def send_message(payload: ChatRequest, request: Request) -> TurnAcceptedOut:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not is_canonical_uuid(payload.session_id):
        # Session ids name transcript files inside the agent's project dir.
        raise HTTPException(status_code=400, detail="invalid session id")
    await _validate_session_owner(request, payload.agent_id, payload.session_id)
    registry = _registry(request)
    try:
        turn_id = await registry.submit(
            Turn(
                session_id=payload.session_id,
                agent_id=payload.agent_id,
                prompt=content,
                source="manual",
            )
        )
    except SessionDeletingError as exc:
        raise HTTPException(
            status_code=409,
            detail="session or agent is being deleted; retry after it finishes",
        ) from exc
    except RuntimeError as exc:
        # The registry rejects work once the application is shutting down.
        raise HTTPException(
            status_code=503, detail="agent runtime is not running"
        ) from exc
    return TurnAcceptedOut(session_id=payload.session_id, turn_id=turn_id)


@router.post("/commands", response_model=ChatCommandResponse)
async def run_command(payload: ChatCommandRequest, request: Request) -> ChatCommandResponse:
    command = commands.parse_web_command(payload.command)
    if command is None:
        raise HTTPException(status_code=400, detail="not a command")
    registry = _registry(request)
    if command in commands.PLATFORM_COMMANDS:
        if command in ("/new", "/clear"):
            reply = commands.NEW_SESSION_TEXT
        elif command == "/help":
            reply = commands.HELP_TEXT
        elif command == "/status":
            if payload.session_id is None:
                reply = commands.NO_SESSION_TEXT
            else:
                busy = registry.is_busy(payload.session_id)
                reply = (
                    f"Agent：{payload.agent_id}\n会话：{payload.session_id}\n"
                    f"状态：{'执行中' if busy else '空闲'}"
                )
        elif command == "/stop":
            if payload.session_id is None:
                reply = commands.STOP_EMPTY_TEXT
            elif await registry.interrupt(payload.session_id):
                reply = commands.STOP_REQUESTED_TEXT
            else:
                reply = commands.STOP_EMPTY_TEXT
        else:  # pragma: no cover - PLATFORM_COMMANDS and this chain must match
            raise HTTPException(
                status_code=500, detail="unhandled platform command"
            )
        return ChatCommandResponse(command=command, reply=reply)

    # Only commands the Claude Code runtime reported are forwarded; everything
    # else gets an answer that lists what the runtime actually offers.
    runner = getattr(request.app.state, "runner", None)
    lookup = getattr(runner, "slash_commands", None)
    known = lookup(payload.agent_id) if lookup is not None else []
    if not known:
        reply = commands.COMMANDS_NOT_READY_TEXT
    else:
        canonical = {
            item.lower().lstrip("/"): item.lstrip("/") for item in known
        }.get(command.lstrip("/"))
        if canonical is not None:
            return ChatCommandResponse(
                command=f"/{canonical}", reply="", forward=True
            )
        reply = commands.unknown_command_text(known)
    return ChatCommandResponse(command=command, reply=reply)
