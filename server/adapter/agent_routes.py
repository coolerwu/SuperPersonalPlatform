from pydantic import BaseModel, ConfigDict
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from server.adapter.auth_routes import SESSION_COOKIE, is_authenticated_request
from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.harness import (
    AgentChatUnavailableError,
    ChatImage,
)
from server.domain.agents import AgentConfigError
from server.domain.sessions import ChatImageData, ChatMessageData


SUPPORTED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentChatImagePayload(StrictPayload):
    mime_type: str
    data: str


class AgentChatMessage(StrictPayload):
    type: str
    agent_id: str | None = None
    content: str | None = None
    images: list[AgentChatImagePayload] = []
    session_id: str | None = None


class AgentModelConfigPayload(StrictPayload):
    id: str
    name: str
    base_url: str
    model: str
    api_key: str | None = None
    temperature: float | None = None
    supports_images: bool = False
    mode: str = "prompt"


class AgentDefinitionConfigPayload(StrictPayload):
    id: str
    name: str
    model_id: str
    system_prompt: str
    skill_ids: list[str] | None = None


class SkillDefinitionConfigPayload(StrictPayload):
    id: str
    name: str = ""
    tools: dict[str, object] | None = None


class SkillContentPayload(StrictPayload):
    id: str
    content: str
    name: str = ""
    tools: dict[str, object] | None = None
    agent_id: str | None = None


class AgentConfigUpdatePayload(StrictPayload):
    default_model_id: str
    skills: list[SkillDefinitionConfigPayload] | None = None
    models: list[AgentModelConfigPayload]
    agents: list[AgentDefinitionConfigPayload]


def create_agent_router(container: AppContainer) -> APIRouter:
    def require_agent_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("/options", dependencies=[Depends(require_agent_auth)])
    def options() -> dict[str, object]:
        service = _agent_service(container)
        try:
            agent_options = service.options()
        except AgentConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        return {
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "model_id": agent.model_id,
                    "model": None
                    if agent.model is None
                    else {
                        "id": agent.model.id,
                        "name": agent.model.name,
                        "model": agent.model.model,
                        "base_url": agent.model.base_url,
                        "mode": agent.model.mode.value,
                        "supports_images": agent.model.supports_images,
                        "has_api_key": agent.model.has_api_key,
                    },
                }
                for agent in agent_options.agents
            ],
        }

    @router.get("/config", dependencies=[Depends(require_agent_auth)])
    def config() -> dict[str, object]:
        service = _agent_service(container)
        try:
            snapshot = service.config_snapshot()
        except AgentConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "path": snapshot.path,
            "default_model_id": snapshot.default_model_id,
            "skills": [
                {
                    "id": skill.id,
                    "name": skill.name,
                    "tools": {
                        "allow": list(skill.tools_allow),
                    },
                    "is_builtin": skill.is_builtin,
                }
                for skill in snapshot.skills
            ],
            "models": [
                {
                    "id": model.id,
                    "name": model.name,
                    "base_url": model.base_url,
                    "model": model.model,
                    "temperature": model.temperature,
                    "supports_images": model.supports_images,
                    "mode": model.mode.value,
                    "has_api_key": model.has_api_key,
                    "api_key_mask": model.api_key_mask,
                }
                for model in snapshot.models
            ],
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "model_id": agent.model_id,
                    "system_prompt": agent.system_prompt,
                    "skill_ids": list(agent.skill_ids),
                    "is_builtin": agent.is_builtin,
                }
                for agent in snapshot.agents
            ],
        }

    @router.get("/tools", dependencies=[Depends(require_agent_auth)])
    def tools() -> dict[str, object]:
        return {"tools": list(_agent_service(container).tool_definitions())}

    @router.put("/config", dependencies=[Depends(require_agent_auth)])
    def update_config(payload: AgentConfigUpdatePayload) -> dict[str, str | bool]:
        service = _agent_service(container)
        payload_data = (
            payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        )
        try:
            service.update_config(payload_data)
        except AgentConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {"ok": True, "message": "Agent 配置已保存"}

    @router.get("/skills/content", dependencies=[Depends(require_agent_auth)])
    def skill_content(
        id: str = Query(...),
        agent_id: str | None = Query(None),
    ) -> dict[str, object]:
        service = _agent_service(container)
        try:
            return service.read_skill_content(id, agent_id)
        except AgentConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @router.put("/skills/content", dependencies=[Depends(require_agent_auth)])
    def update_skill_content(payload: SkillContentPayload) -> dict[str, object]:
        service = _agent_service(container)
        try:
            return service.write_skill_content(
                payload.id,
                payload.content,
                payload.agent_id,
                name=payload.name,
                tools=payload.tools,
            )
        except AgentConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @router.websocket("/chat/connect")
    async def connect_chat(websocket: WebSocket) -> None:
        service = container.agent_chat_service
        if service is None:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return
        session_cookie = websocket.cookies.get(SESSION_COOKIE)
        if not is_authenticated_request(container, session_cookie):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        await websocket.send_json({"type": "status", "status": "connected"})
        try:
            while True:
                raw = await websocket.receive_json()
                payload = AgentChatMessage(**raw)
                if payload.type != "message":
                    await websocket.send_json({"type": "error", "message": "不支持的消息类型"})
                    continue

                try:
                    images = _parse_images(payload.images)
                except AgentConfigError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                if not (payload.content or "").strip() and not images:
                    await websocket.send_json({"type": "error", "message": "消息内容不能为空"})
                    continue

                if payload.session_id and container.chat_session_service is not None:
                    try:
                        container.chat_session_service.get_session(
                            payload.session_id,
                            (payload.agent_id or "").strip(),
                        )
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        continue

                await websocket.send_json({"type": "status", "status": "running"})
                try:
                    async def send_checkpoint(checkpoint) -> None:
                        await websocket.send_json(
                            {
                                "type": "checkpoint",
                                "stage": checkpoint.stage,
                                "title": checkpoint.title,
                                "detail": checkpoint.detail,
                            }
                        )

                    message = await service.chat(
                        payload.agent_id or "",
                        payload.content or "",
                        images,
                        on_checkpoint=send_checkpoint,
                    )
                except (AgentConfigError, AgentChatUnavailableError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    await websocket.send_json({"type": "status", "status": "idle"})
                    continue
                except Exception:
                    await websocket.send_json({"type": "error", "message": "Agent 回复失败"})
                    await websocket.send_json({"type": "status", "status": "idle"})
                    continue

                await websocket.send_json({"type": "assistant_message", "content": message})
                await websocket.send_json({"type": "status", "status": "idle"})

                if payload.session_id and container.chat_session_service is not None:
                    _save_session_messages(
                        container.chat_session_service,
                        payload.session_id,
                        payload.content or "",
                        images,
                        message,
                    )
        except WebSocketDisconnect:
            return

    return router


def _agent_service(container: AppContainer):
    service = container.agent_chat_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent 服务不可用",
        )
    return service


def _save_session_messages(session_service, session_id: str, user_content: str, images: tuple[ChatImage, ...], assistant_content: str) -> None:
    try:
        image_data = tuple(
            ChatImageData(mime_type=img.mime_type, data=img.data) for img in images
        )
        session_service.append_message(
            session_id,
            ChatMessageData(role="user", content=user_content, images=image_data),
        )
        session_service.append_message(
            session_id,
            ChatMessageData(role="assistant", content=assistant_content),
        )
    except Exception:
        pass


def _parse_images(images: list[AgentChatImagePayload]) -> tuple[ChatImage, ...]:
    parsed: list[ChatImage] = []
    for image in images:
        mime_type = image.mime_type.strip().lower()
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise AgentConfigError("不支持的图片类型")
        if not image.data.strip():
            raise AgentConfigError("图片内容不能为空")
        parsed.append(ChatImage(mime_type=mime_type, data=image.data.strip()))
    return tuple(parsed)
