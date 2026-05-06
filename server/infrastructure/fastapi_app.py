import os
from pathlib import Path

from fastapi import FastAPI

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.proxy_routes import (
    create_api_fallback_proxy_router,
    create_proxy_router,
    create_root_asset_proxy_router,
)
from server.adapter.static_routes import mount_frontend
from server.adapter.system_routes import create_system_router
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.domain.auth import AuthToken
from server.infrastructure.config import Settings, load_settings
from server.infrastructure.http_proxy_gateway import HttpProxyGateway
from server.infrastructure.session import SessionCodec


def current_workspace() -> Path:
    return Path(os.environ.get("SUPER_PERSONAL_WORKSPACE", Path.cwd())).resolve()


def create_container(settings: Settings) -> AppContainer:
    project_root = Path(__file__).resolve().parents[2]
    workspace = current_workspace()
    system_log_service = SystemLogService(workspace)
    return AppContainer(
        auth_service=AuthService(AuthToken(settings.auth.token)),
        config_file_service=ConfigFileService(workspace),
        proxy_service=ProxyService(HttpProxyGateway(settings.proxy.upstream_base_url)),
        system_log_service=system_log_service,
        system_update_service=SystemUpdateService(project_root, workspace, system_log_service),
        session_codec=SessionCodec(settings.auth.token),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings(current_workspace() / "config.yaml")

    container = create_container(settings)
    app = FastAPI(title="Super Personal Platform")
    app.include_router(create_auth_router(container))
    app.include_router(create_proxy_router(container))
    app.include_router(create_system_router(container))
    app.include_router(create_api_fallback_proxy_router(container))
    app.include_router(create_root_asset_proxy_router(container))

    project_root = Path(__file__).resolve().parents[2]
    mount_frontend(app, container, project_root / "web" / "dist")
    return app
