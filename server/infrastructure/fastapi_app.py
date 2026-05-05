import os
from pathlib import Path

from fastapi import FastAPI

from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.proxy_routes import create_proxy_router
from server.adapter.static_routes import mount_frontend
from server.app.auth_service import AuthService
from server.app.logs_service import LogsService
from server.domain.auth import AuthToken
from server.infrastructure.config import Settings, load_settings
from server.infrastructure.logs_http_client import HttpLogsGateway
from server.infrastructure.session import SessionCodec


def create_container(settings: Settings) -> AppContainer:
    return AppContainer(
        auth_service=AuthService(AuthToken(settings.auth.token)),
        logs_service=LogsService(HttpLogsGateway(settings.proxy.logs_url)),
        session_codec=SessionCodec(settings.auth.token),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        config_path = os.environ.get("SUPER_PERSONAL_CONFIG", "config.yaml")
        settings = load_settings(config_path)

    container = create_container(settings)
    app = FastAPI(title="Super Personal Platform")
    app.include_router(create_auth_router(container))
    app.include_router(create_proxy_router(container))

    project_root = Path(__file__).resolve().parents[2]
    mount_frontend(app, container, project_root / "web" / "dist")
    return app
