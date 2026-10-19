from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import Agent, Message, Provider, Run, RunEvent, Session
from app.schemas import AgentCreate, AgentOut, AgentUpdate

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_auth)])


def agent_out(agent: Agent, provider: Provider | None) -> AgentOut:
    return AgentOut(
        id=agent.id,
        name=agent.name,
        instructions=agent.instructions,
        provider_id=agent.provider_id,
        provider_name=provider.name if provider else "",
        model=provider.model if provider else "",
        status=agent.status,
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
    agent = Agent(
        name=payload.name.strip(),
        instructions=payload.instructions,
        provider_id=provider.id,
        status="active",
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
    await db.commit()
    await db.refresh(agent)
    provider = await db.get(Provider, agent.provider_id)
    return agent_out(agent, provider)


@router.delete("/{agent_id}/permanent")
async def delete_agent_permanently(agent_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    active = await db.scalar(
        select(Run).where(Run.agent_id == agent_id, Run.status.in_(("queued", "running"))).limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="agent has an active run")

    session_ids = list((await db.scalars(select(Session.id).where(Session.agent_id == agent_id))).all())
    run_ids = list((await db.scalars(select(Run.id).where(Run.agent_id == agent_id))).all())
    if run_ids:
        await db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    if session_ids:
        await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
        await db.execute(delete(Session).where(Session.id.in_(session_ids)))
    await db.delete(agent)
    await db.commit()

    for session_id in session_ids:
        _remove_checkpoint(session_id)
    return {"deleted": True, "sessions": len(session_ids), "runs": len(run_ids)}


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


def _remove_checkpoint(session_id: str) -> None:
    base = settings.checkpoint_dir / f"{session_id}.sqlite"
    for path in (base, Path(f"{base}-wal"), Path(f"{base}-shm")):
        path.unlink(missing_ok=True)
