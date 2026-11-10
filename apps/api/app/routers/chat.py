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
from app.services.claude_runner import Turn
from app.services.identifiers import is_canonical_uuid

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])


def _registry(request: Request):
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    return registry


@router.post("/messages", response_model=TurnAcceptedOut, status_code=201)
async def send_message(payload: ChatRequest, request: Request) -> TurnAcceptedOut:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not is_canonical_uuid(payload.session_id):
        # Session ids name transcript files inside the agent's project dir.
        raise HTTPException(status_code=400, detail="invalid session id")
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
    except RuntimeError as exc:
        # The registry rejects work once the application is shutting down.
        raise HTTPException(
            status_code=503, detail="agent runtime is not running"
        ) from exc
    return TurnAcceptedOut(session_id=payload.session_id, turn_id=turn_id)


@router.post("/commands", response_model=ChatCommandResponse)
async def run_command(payload: ChatCommandRequest, request: Request) -> ChatCommandResponse:
    command = commands.parse_command(payload.command)
    if command is None:
        raise HTTPException(status_code=400, detail="not a command")
    registry = _registry(request)
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
    else:
        reply = commands.UNKNOWN_COMMAND_TEXT
    return ChatCommandResponse(command=command, reply=reply)
