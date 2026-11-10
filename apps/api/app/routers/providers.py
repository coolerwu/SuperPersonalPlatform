from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth
from app.schemas import ProviderCreate, ProviderOut, ProviderUpdate
from app.services.settings_store import (
    AgentSection,
    ProviderSection,
    SettingsStore,
    get_settings_store,
)

router = APIRouter(prefix="/api/providers", tags=["providers"], dependencies=[Depends(require_auth)])

logger = logging.getLogger(__name__)

PROVIDER_ID = "default"


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


async def _close_agent_clients(request: Request, agent_id: str) -> None:
    """Drop cached SDK clients so the next turn picks up a new provider."""
    registry = getattr(request.app.state, "registry", None)
    close = getattr(registry, "close_agent_clients", None)
    if close is None:
        return
    try:
        await close(agent_id)
    except Exception:
        logger.exception("closing cached clients failed agent=%s", agent_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_agent(agents: list[AgentSection]) -> AgentSection | None:
    # The provider page edits the default Agent's provider. Other Agents keep
    # their own provider copies in settings.json.
    return next((item for item in agents if item.id == "default"), agents[0] if agents else None)


def _normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized and not normalized.endswith("/anthropic"):
        normalized += "/anthropic"
    return normalized


def _provider_out(agent: AgentSection) -> ProviderOut:
    created = agent.created_at or _now().isoformat()
    return ProviderOut(
        id=PROVIDER_ID,
        name=agent.provider.name,
        base_url=agent.provider.base_url,
        model=agent.provider.model,
        has_api_key=bool(agent.provider.api_key),
        created_at=created,
        updated_at=agent.updated_at or created,
    )


async def _require_default_agent(store: SettingsStore) -> AgentSection:
    snapshot = await store.get()
    agent = _default_agent(snapshot.agents)
    if agent is None:
        raise HTTPException(status_code=400, detail="configure an agent first")
    return agent


@router.get("", response_model=list[ProviderOut])
async def list_providers(request: Request) -> list[ProviderOut]:
    snapshot = await _store(request).get()
    agent = _default_agent(snapshot.agents)
    return [_provider_out(agent)] if agent is not None else []


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(payload: ProviderCreate, request: Request) -> ProviderOut:
    name = payload.name.strip()
    base_url = _normalize_base_url(payload.base_url)
    model = payload.model.strip()
    if not name or not base_url or not model:
        raise HTTPException(
            status_code=400, detail="name, base_url, and model are required"
        )
    store = _store(request)
    agent = await _require_default_agent(store)
    provider = ProviderSection(
        name=name,
        base_url=base_url,
        model=model,
        # Creating the singleton provider must not silently erase a key the
        # user did not retype.
        api_key=payload.api_key.strip() or agent.provider.api_key,
    )
    now = _now().isoformat()
    await store.update_agent_fields(
        agent.id, provider=provider.model_dump(), updated_at=now
    )
    await _close_agent_clients(request, agent.id)
    return _provider_out(
        agent.model_copy(update={"provider": provider, "updated_at": now})
    )


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str, payload: ProviderUpdate, request: Request
) -> ProviderOut:
    if provider_id != PROVIDER_ID:
        raise HTTPException(status_code=404, detail="provider not found")
    store = _store(request)
    agent = await _require_default_agent(store)

    provider = agent.provider.model_copy()
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] and str(data["name"]).strip():
        provider.name = str(data["name"]).strip()
    if "base_url" in data and data["base_url"] is not None:
        provider.base_url = _normalize_base_url(str(data["base_url"]))
    if "model" in data and data["model"] and str(data["model"]).strip():
        provider.model = str(data["model"]).strip()
    if "api_key" in data and data["api_key"] and str(data["api_key"]).strip():
        provider.api_key = str(data["api_key"]).strip()

    now = _now().isoformat()
    await store.update_agent_fields(
        agent.id, provider=provider.model_dump(), updated_at=now
    )
    await _close_agent_clients(request, agent.id)
    return _provider_out(
        agent.model_copy(update={"provider": provider, "updated_at": now})
    )
