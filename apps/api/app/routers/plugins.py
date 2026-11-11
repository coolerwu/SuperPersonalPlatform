from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.schemas import (
    AvailablePluginOut,
    InstalledPluginOut,
    PluginInstallRequest,
    PluginMarketplaceOut,
    PluginsCatalogOut,
)
from app.services import plugins as plugin_service
from app.services.plugins import (
    PluginBusyError,
    PluginCommandError,
    PluginConflictError,
    PluginError,
    PluginNotFoundError,
    PluginUnavailableError,
    PluginValidationError,
)

router = APIRouter(
    prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(require_auth)]
)


def _catalog_out(catalog: plugin_service.PluginCatalog) -> PluginsCatalogOut:
    return PluginsCatalogOut(
        cli_available=catalog.cli_available,
        marketplaces=[
            PluginMarketplaceOut(
                name=item.name,
                repo=item.repo,
                install_location=item.install_location,
            )
            for item in catalog.marketplaces
        ],
        installed=[
            InstalledPluginOut(
                id=item.id,
                name=item.name,
                marketplace=item.marketplace,
                version=item.version,
                enabled=item.enabled,
                scope=item.scope,
                installed_at=item.installed_at,
                last_updated=item.last_updated,
            )
            for item in catalog.installed
        ],
        available=[
            AvailablePluginOut(
                name=item.name,
                description=item.description,
                author=item.author,
                category=item.category,
                homepage=item.homepage,
            )
            for item in catalog.available
        ],
        errors=list(catalog.errors),
    )


def _http_error(exc: PluginError) -> HTTPException:
    if isinstance(exc, PluginUnavailableError):
        status = 503
    elif isinstance(exc, PluginNotFoundError):
        status = 404
    elif isinstance(exc, PluginConflictError):
        status = 409
    elif isinstance(exc, PluginBusyError):
        status = 409
    elif isinstance(exc, PluginValidationError):
        status = 422
    elif isinstance(exc, PluginCommandError):
        status = 502
    else:
        status = 500
    return HTTPException(status_code=status, detail=str(exc))


@router.get("", response_model=PluginsCatalogOut)
async def list_plugins() -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.catalog())
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/marketplace", response_model=PluginsCatalogOut)
async def add_marketplace() -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.add_official_marketplace())
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/marketplace/update", response_model=PluginsCatalogOut)
async def update_marketplace() -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.update_official_marketplace())
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.delete("/marketplace", response_model=PluginsCatalogOut)
async def remove_marketplace() -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.remove_official_marketplace())
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/install", response_model=PluginsCatalogOut)
async def install_plugin(payload: PluginInstallRequest) -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.install_plugin(payload.name))
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/{plugin_id}/enable", response_model=PluginsCatalogOut)
async def enable_plugin(plugin_id: str) -> PluginsCatalogOut:
    try:
        return _catalog_out(
            await plugin_service.set_plugin_enabled(plugin_id, True)
        )
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/{plugin_id}/disable", response_model=PluginsCatalogOut)
async def disable_plugin(plugin_id: str) -> PluginsCatalogOut:
    try:
        return _catalog_out(
            await plugin_service.set_plugin_enabled(plugin_id, False)
        )
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.post("/{plugin_id}/update", response_model=PluginsCatalogOut)
async def update_plugin(plugin_id: str) -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.update_plugin(plugin_id))
    except PluginError as exc:
        raise _http_error(exc) from exc


@router.delete("/{plugin_id}", response_model=PluginsCatalogOut)
async def uninstall_plugin(plugin_id: str) -> PluginsCatalogOut:
    try:
        return _catalog_out(await plugin_service.uninstall_plugin(plugin_id))
    except PluginError as exc:
        raise _http_error(exc) from exc
