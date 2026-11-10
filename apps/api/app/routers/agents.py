from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import ChannelDelivery, ChannelInbound, CronJob
from app.prompts import DEFAULT_AGENT_INSTRUCTIONS
from app.schemas import AgentCreate, AgentOut, AgentUpdate, AttachmentOut
from app.services.attachments import (
    MAX_ATTACHMENT_BYTES,
    new_stored_name,
    resolve_upload_file,
    safe_attachment_name,
    save_attachment_bytes,
)
from app.services.claude_env import workspace_for
from app.services.claude_runner import transcript_dir
from app.services.identifiers import valid_agent_id
from app.services.settings_store import (
    AgentSection,
    ProviderSection,
    SettingsStore,
    build_default_settings,
    get_settings_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_auth)])


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_out(agent: AgentSection) -> AgentOut:
    created = agent.created_at or _now_iso()
    return AgentOut(
        id=agent.id,
        name=agent.name,
        instructions=agent.instructions,
        provider_id="default",
        provider_name=agent.provider.name,
        model=agent.provider.model,
        status=agent.status,
        auto_memory=agent.auto_memory,
        enabled_skills=list(agent.enabled_skills),
        created_at=created,
        updated_at=agent.updated_at or created,
    )


def _find_agent(
    agents: list[AgentSection], agent_id: str, *, include_archived: bool = False
) -> AgentSection | None:
    agent = next((item for item in agents if item.id == agent_id), None)
    if agent is None:
        return None
    if not include_archived and agent.status == "archived":
        return None
    return agent


def _fallback_provider(agents: list[AgentSection]) -> ProviderSection:
    if agents:
        return agents[0].provider
    raw = build_default_settings()["agents"][0]["provider"]
    return ProviderSection.model_validate(raw)


async def _agent_session_ids(
    snapshot, agent_id: str, db: AsyncSession
) -> set[str]:
    session_ids: set[str] = set()
    if valid_agent_id(agent_id):
        directory = transcript_dir(agent_id)
        if directory.is_dir():
            session_ids.update(path.stem for path in directory.glob("*.jsonl"))
    channel_ids = {
        channel.id for channel in snapshot.channels if channel.agent_id == agent_id
    }
    for conversation in snapshot.channel_conversations.values():
        if conversation.channel_id in channel_ids:
            session_ids.add(conversation.session_id)
    job_sessions = (
        await db.scalars(
            select(CronJob.session_id).where(
                CronJob.agent_id == agent_id, CronJob.session_id.isnot(None)
            )
        )
    ).all()
    session_ids.update(str(item) for item in job_sessions if item)
    return session_ids


def _registry_is_busy(
    request: Request, agent_id: str, session_ids: set[str]
) -> bool:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return False
    agent_check = getattr(registry, "is_agent_busy", None)
    if agent_check is not None and agent_check(agent_id):
        return True
    return any(registry.is_busy(session_id) for session_id in session_ids)


async def _agent_has_active_turn(
    request: Request, snapshot, agent_id: str, db: AsyncSession
) -> bool:
    session_ids = await _agent_session_ids(snapshot, agent_id, db)
    return _registry_is_busy(request, agent_id, session_ids)


@router.get("", response_model=list[AgentOut])
async def list_agents(request: Request) -> list[AgentOut]:
    snapshot = await _store(request).get()
    return [
        _agent_out(agent)
        for agent in snapshot.agents
        if agent.status != "archived"
    ]


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(payload: AgentCreate, request: Request) -> AgentOut:
    store = _store(request)
    snapshot = await store.get()
    if payload.provider_id and payload.provider_id != "default":
        raise HTTPException(status_code=400, detail="provider not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    agent_id = uuid.uuid4().hex
    workspace_for(agent_id).mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    agent = AgentSection(
        id=agent_id,
        name=name,
        instructions=payload.instructions.strip() or DEFAULT_AGENT_INSTRUCTIONS,
        status="active",
        enabled_skills=payload.enabled_skills,
        provider=_fallback_provider(snapshot.agents),
        auto_memory=payload.auto_memory,
        created_at=now,
        updated_at=now,
    )
    await store.update_agent(agent)
    return _agent_out(agent)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, request: Request) -> AgentOut:
    snapshot = await _store(request).get()
    agent = _find_agent(snapshot.agents, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return _agent_out(agent)


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, payload: AgentUpdate, request: Request
) -> AgentOut:
    store = _store(request)
    snapshot = await store.get()
    agent = _find_agent(snapshot.agents, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    data = payload.model_dump(exclude_unset=True)
    fields: dict[str, object] = {}
    if "name" in data and data["name"]:
        name = str(data["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        fields["name"] = name
    if "instructions" in data and data["instructions"] is not None:
        fields["instructions"] = str(data["instructions"])
    if "provider_id" in data and data["provider_id"]:
        if str(data["provider_id"]) != "default":
            raise HTTPException(status_code=400, detail="provider not found")
    if "status" in data and data["status"] in ("active", "paused"):
        fields["status"] = str(data["status"])
    if "auto_memory" in data and data["auto_memory"] is not None:
        fields["auto_memory"] = bool(data["auto_memory"])
    if "enabled_skills" in data and data["enabled_skills"] is not None:
        fields["enabled_skills"] = list(data["enabled_skills"])
    if fields:
        fields["updated_at"] = _now_iso()
        await store.update_agent_fields(agent_id, **fields)
    updated = agent.model_copy(update=fields) if fields else agent
    return _agent_out(updated)


@router.delete("/{agent_id}/permanent")
async def delete_agent_permanently(
    agent_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    store = _store(request)
    snapshot = await store.get()
    agent = _find_agent(snapshot.agents, agent_id, include_archived=True)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    session_ids = await _agent_session_ids(snapshot, agent_id, db)
    if _registry_is_busy(request, agent_id, session_ids):
        raise HTTPException(status_code=409, detail="agent has an active run")

    channel_ids = {
        channel.id for channel in snapshot.channels if channel.agent_id == agent_id
    }
    runtime = getattr(request.app.state, "weixin", None)
    if runtime is not None:
        for channel_id in channel_ids:
            await runtime.stop_channel(channel_id)
            runtime.clear_login(channel_id)

    session_directory = transcript_dir(agent_id) if valid_agent_id(agent_id) else None
    session_count = (
        len(list(session_directory.glob("*.jsonl")))
        if session_directory is not None and session_directory.is_dir()
        else 0
    )

    if channel_ids:
        channel_id_list = list(channel_ids)
        await db.execute(
            delete(ChannelDelivery).where(ChannelDelivery.channel_id.in_(channel_id_list))
        )
        await db.execute(
            delete(ChannelInbound).where(ChannelInbound.channel_id.in_(channel_id_list))
        )
    await db.execute(delete(CronJob).where(CronJob.agent_id == agent_id))
    await db.commit()

    registry = getattr(request.app.state, "registry", None)
    if registry is not None:
        for session_id in session_ids:
            try:
                await registry.close_session(session_id)
            except Exception:
                logger.warning(
                    "failed to release cached session %s", session_id, exc_info=True
                )

    def remove_from_settings(data: dict) -> None:
        agents = data.get("agents")
        if isinstance(agents, list):
            data["agents"] = [
                item
                for item in agents
                if not (isinstance(item, dict) and item.get("id") == agent_id)
            ]
        channels = data.get("channels")
        removed_channels: set[str] = set()
        if isinstance(channels, list):
            for item in channels:
                if isinstance(item, dict) and item.get("agent_id") == agent_id:
                    removed_channels.add(str(item.get("id") or ""))
            data["channels"] = [
                item
                for item in channels
                if not (
                    isinstance(item, dict) and item.get("agent_id") == agent_id
                )
            ]
        conversations = data.get("channel_conversations")
        if isinstance(conversations, dict):
            for key, value in list(conversations.items()):
                if (
                    isinstance(value, dict)
                    and value.get("channel_id") in removed_channels
                ):
                    conversations.pop(key, None)

    await store.mutate_raw(remove_from_settings)

    if valid_agent_id(agent_id):
        shutil.rmtree(workspace_for(agent_id), ignore_errors=True)
        shutil.rmtree(transcript_dir(agent_id), ignore_errors=True)
    else:
        logger.warning("not deleting directories for invalid agent id %r", agent_id)
    return {"deleted": True, "sessions": session_count, "runs": 0}


@router.post("/{agent_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    agent_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=200),
) -> AttachmentOut:
    snapshot = await _store(request).get()
    if _find_agent(snapshot.agents, agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="attachment is too large")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="attachment is empty")
    if len(body) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="attachment is too large")

    display_name = safe_attachment_name(filename)
    stored_name = new_stored_name(display_name)
    saved = save_attachment_bytes(agent_id, stored_name, body)
    return AttachmentOut(name=display_name, path=saved.path, size=saved.size)


@router.get("/{agent_id}/uploads/{filename}")
async def get_upload(
    agent_id: str, filename: str, request: Request
) -> FileResponse:
    snapshot = await _store(request).get()
    if _find_agent(snapshot.agents, agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    path = resolve_upload_file(agent_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/{agent_id}")
async def archive_agent(
    agent_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    store = _store(request)
    snapshot = await store.get()
    agent = _find_agent(snapshot.agents, agent_id, include_archived=True)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if await _agent_has_active_turn(request, snapshot, agent_id, db):
        raise HTTPException(status_code=409, detail="agent has an active run")
    await store.update_agent_fields(
        agent_id, status="archived", updated_at=_now_iso()
    )
    return {"archived": True}
