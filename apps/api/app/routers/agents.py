from __future__ import annotations

import shutil
import unicodedata
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import (
    Agent,
    Channel,
    MemoryItem,
    Message,
    Provider,
    Run,
    RunEvent,
    RunMemory,
    Session,
    new_id,
)
from app.prompts import DEFAULT_AGENT_INSTRUCTIONS
from app.schemas import AgentCreate, AgentOut, AgentUpdate, AttachmentOut
from app.services import channel_service
from app.services.workspace import WorkspaceError, resolve_workspace_path

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_auth)])

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_NAME_BYTES = 180


def _safe_attachment_name(raw: str) -> str:
    value = unicodedata.normalize("NFC", str(raw or "").replace("\\", "/"))
    name = Path(value).name.strip()
    name = "".join(char for char in name if unicodedata.category(char) not in ("Cc", "Cf"))
    name = name.lstrip(".") or "attachment"
    while len(name.encode("utf-8")) > MAX_ATTACHMENT_NAME_BYTES:
        name = name[:-1]
    return name or "attachment"


def agent_out(agent: Agent, provider: Provider | None) -> AgentOut:
    return AgentOut(
        id=agent.id,
        name=agent.name,
        instructions=agent.instructions,
        provider_id=agent.provider_id,
        provider_name=provider.name if provider else "",
        model=provider.model if provider else "",
        status=agent.status,
        auto_memory=agent.auto_memory,
        enabled_skills=list(agent.enabled_skills or []),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _default_provider(db: AsyncSession) -> Provider | None:
    return await db.scalar(select(Provider).order_by(Provider.created_at).limit(1))


@router.get("", response_model=list[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentOut]:
    agents = (await db.scalars(select(Agent).where(Agent.status != "archived").order_by(Agent.created_at))).all()
    providers = {p.id: p for p in (await db.scalars(select(Provider))).all()}
    return [agent_out(agent, providers.get(agent.provider_id)) for agent in agents]


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> AgentOut:
    provider = await db.get(Provider, payload.provider_id) if payload.provider_id else await _default_provider(db)
    if provider is None:
        raise HTTPException(status_code=400, detail="configure a provider first")
    agent_id = new_id()
    resolve_workspace_path(agent_id, create=True)
    agent = Agent(
        id=agent_id,
        name=payload.name.strip(),
        instructions=payload.instructions.strip() or DEFAULT_AGENT_INSTRUCTIONS,
        provider_id=provider.id,
        status="active",
        auto_memory=payload.auto_memory,
        enabled_skills=payload.enabled_skills,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent_out(agent, provider)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentOut:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    provider = await db.get(Provider, agent.provider_id)
    return agent_out(agent, provider)


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        agent.name = str(data["name"]).strip()
    if "instructions" in data and data["instructions"] is not None:
        agent.instructions = str(data["instructions"])
    if "provider_id" in data and data["provider_id"]:
        provider = await db.get(Provider, str(data["provider_id"]))
        if provider is None:
            raise HTTPException(status_code=400, detail="provider not found")
        agent.provider_id = provider.id
    if "status" in data and data["status"] in ("active", "paused"):
        agent.status = str(data["status"])
    if "auto_memory" in data and data["auto_memory"] is not None:
        agent.auto_memory = bool(data["auto_memory"])
    if "enabled_skills" in data and data["enabled_skills"] is not None:
        agent.enabled_skills = list(data["enabled_skills"])
    await db.commit()
    await db.refresh(agent)
    provider = await db.get(Provider, agent.provider_id)
    return agent_out(agent, provider)


@router.delete("/{agent_id}/permanent")
async def delete_agent_permanently(
    agent_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    active = await db.scalar(
        select(Run).where(Run.agent_id == agent_id, Run.status.in_(("queued", "running"))).limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="agent has an active run")

    channel_ids = list(
        (await db.scalars(select(Channel.id).where(Channel.agent_id == agent_id))).all()
    )
    runtime = getattr(request.app.state, "weixin", None)
    if runtime is not None:
        for channel_id in channel_ids:
            await runtime.stop_channel(channel_id)
            runtime.clear_login(channel_id)
    await channel_service.delete_channels(db, channel_ids)

    session_ids = list((await db.scalars(select(Session.id).where(Session.agent_id == agent_id))).all())
    run_ids = list((await db.scalars(select(Run.id).where(Run.agent_id == agent_id))).all())
    memory_ids = list(
        (
            await db.scalars(
                select(MemoryItem.id).where(
                    MemoryItem.scope == "agent", MemoryItem.agent_id == agent_id
                )
            )
        ).all()
    )
    if run_ids:
        await db.execute(delete(RunMemory).where(RunMemory.run_id.in_(run_ids)))
    if memory_ids:
        await db.execute(delete(RunMemory).where(RunMemory.memory_id.in_(memory_ids)))
    if run_ids:
        await db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    if memory_ids:
        await db.execute(delete(MemoryItem).where(MemoryItem.id.in_(memory_ids)))
    if session_ids:
        await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
        await db.execute(delete(Session).where(Session.id.in_(session_ids)))
    await db.delete(agent)
    await db.commit()

    for session_id in session_ids:
        _remove_checkpoint(agent_id, session_id)
    return {"deleted": True, "sessions": len(session_ids), "runs": len(run_ids)}


@router.post("/{agent_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    agent_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
) -> AttachmentOut:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.status == "archived":
        raise HTTPException(status_code=404, detail="agent not found")

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="attachment is too large")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="attachment is empty")
    if len(body) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="attachment is too large")

    uploads = resolve_workspace_path(f"{agent_id}/uploads", create=True)
    display_name = _safe_attachment_name(filename)
    stored_name = f"{uuid.uuid4().hex[:10]}-{display_name}"
    target = uploads / stored_name
    try:
        with target.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        stored_name = f"{uuid.uuid4().hex}-{display_name}"
        target = uploads / stored_name
        with target.open("xb") as handle:
            handle.write(body)

    return AttachmentOut(name=display_name, path=f"/uploads/{stored_name}", size=len(body))


@router.delete("/{agent_id}")
async def archive_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    active = await db.scalar(
        select(Run).where(Run.agent_id == agent_id, Run.status.in_(("queued", "running"))).limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="agent has an active run")
    agent.status = "archived"
    await db.commit()
    return {"archived": True}


def _remove_checkpoint(agent_id: str, session_id: str) -> None:
    base = settings.checkpoint_dir / f"{session_id}.sqlite"
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        path.unlink(missing_ok=True)
    try:
        workspace = resolve_workspace_path(agent_id, create=False)
    except WorkspaceError:
        return
    screenshots = workspace / "screenshots" / session_id
    if screenshots.exists():
        shutil.rmtree(screenshots, ignore_errors=True)
