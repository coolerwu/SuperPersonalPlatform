from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from server.adapter.security import require_authenticated
from server.app.chat_group_service import GroupConflict
from server.domain.chat_group import GroupDefinition


class GroupMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=100000)
    mentions: list[str] = Field(default_factory=list, max_length=20)
    client_message_id: str = Field(min_length=1, max_length=128)


class ContinueRequest(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=128)


def create_chat_group_router(container):
    def auth(request: Request):
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/chat-groups", tags=["chat-groups"], dependencies=[Depends(auth)])
    service = container.chat_group_service

    async def call(operation):
        try:
            return await operation
        except FileNotFoundError as exc:
            raise HTTPException(404, "群不存在") from exc
        except GroupConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def detail(group_id):
        try:
            return service.detail(group_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "群不存在") from exc

    @router.get("")
    def list_groups():
        return {"groups": service.list()}

    @router.post("")
    async def create(payload: GroupDefinition):
        return await call(service.create(payload))

    @router.get("/{group_id}")
    def get(group_id: str):
        return detail(group_id)

    @router.put("/{group_id}")
    async def update(group_id: str, payload: GroupDefinition):
        return await call(service.update(group_id, payload))

    @router.get("/{group_id}/messages")
    def messages(group_id: str, after: int = 0):
        return {"messages": [m for m in detail(group_id)["messages"] if m["seq"] > after]}

    @router.post("/{group_id}/messages")
    async def send(group_id: str, payload: GroupMessageRequest):
        return await call(service.send(group_id, payload.content, payload.mentions, payload.client_message_id))

    @router.post("/{group_id}/collaborations")
    async def collaborate(group_id: str, payload: GroupMessageRequest):
        return await call(service.send(group_id, payload.content, payload.mentions, payload.client_message_id, automatic=True))

    @router.post("/{group_id}/collaborations/{execution_id}/continue")
    async def continue_execution(group_id: str, execution_id: str, payload: ContinueRequest):
        return await call(service.action(group_id, execution_id, "continue", payload.client_message_id))

    @router.post("/{group_id}/collaborations/{execution_id}/{action}")
    async def control(group_id: str, execution_id: str, action: Literal["retry", "stop"]):
        return await call(service.action(group_id, execution_id, action))

    return router
