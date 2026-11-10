from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import (
    ChatCommandRequest,
    ChatCommandResponse,
    ChatRequest,
    TurnAcceptedOut,
)
from app.services import chat_commands
from app.services.claude_runner import Turn

router = APIRouter(prefix="/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])

HELP_TEXT = (
    "可用命令：\n"
    "/new 开始新会话（旧会话保留在会话列表）\n"
    "/clear 同 /new\n"
    "/status 查看当前会话与执行状态\n"
    "/stop 停止当前任务\n"
    "/help 显示本说明\n\n"
    "直接发送其他内容即可与 Agent 对话。"
)


@router.post("/messages", response_model=TurnAcceptedOut, status_code=201)
async def send_message(payload: ChatRequest, request: Request) -> TurnAcceptedOut:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    turn_id = await registry.submit(
        Turn(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            prompt=content,
            source="manual",
        )
    )
    return TurnAcceptedOut(session_id=payload.session_id, turn_id=turn_id)


@router.post("/commands", response_model=ChatCommandResponse)
async def run_command(payload: ChatCommandRequest, request: Request) -> ChatCommandResponse:
    command = chat_commands.parse_command(payload.command)
    if command is None:
        raise HTTPException(status_code=400, detail="not a command")
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="agent runtime is not running")
    if command in ("/new", "/clear"):
        reply = "已开始新会话，之前的对话不会带入；旧会话仍保留在会话列表。"
    elif command == "/help":
        reply = HELP_TEXT
    elif command == "/status":
        busy = registry.is_busy(payload.session_id)
        reply = (
            f"Agent：{payload.agent_id}\n会话：{payload.session_id}\n"
            f"状态：{'执行中' if busy else '空闲'}"
        )
    elif command == "/stop":
        stopped = await registry.interrupt(payload.session_id)
        reply = "已请求停止当前任务。" if stopped else "当前没有正在执行的任务。"
    else:
        reply = f"未知命令。\n\n{HELP_TEXT}"
    return ChatCommandResponse(command=command, reply=reply)
