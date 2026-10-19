from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_db
from app.models import Provider
from app.schemas import ProviderOut, ProviderUpdate

router = APIRouter(prefix="/api/providers", tags=["providers"], dependencies=[Depends(require_auth)])


def provider_out(provider: Provider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        model=provider.model,
        has_api_key=bool(provider.api_key),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("", response_model=list[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[ProviderOut]:
    providers = (await db.scalars(select(Provider).order_by(Provider.created_at))).all()
    return [provider_out(provider) for provider in providers]


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        provider.name = str(data["name"])
    if "base_url" in data and data["base_url"] is not None:
        provider.base_url = str(data["base_url"])
    if "model" in data and data["model"]:
        provider.model = str(data["model"])
    if "api_key" in data and data["api_key"]:
        provider.api_key = str(data["api_key"])
    await db.commit()
    await db.refresh(provider)
    return provider_out(provider)
