import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from server.adapter.agent_routes import create_agent_router
from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.adapter.portfolio_routes import create_portfolio_router
from server.adapter.proxy_routes import (
    create_api_fallback_proxy_router,
    create_proxy_router,
    create_root_asset_proxy_router,
)
from server.adapter.session_routes import create_session_router
from server.adapter.static_routes import mount_frontend
from server.adapter.system_routes import create_system_router
from server.adapter.channel_routes import create_channel_router
from server.adapter.critique_routes import create_critique_router
from server.app.auth_service import AuthService
from server.app.agent_chat_service import AgentChatService
from server.app.agent_run_state_service import AgentRunStateService
from server.app.chat_session_service import ChatSessionService
from server.app.config_file_service import ConfigFileService
from server.app.critique_service import CritiqueService
from server.app.portfolio_service import PortfolioService
from server.app.proxy_service import ProxyService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.wechat_channel_manager import WechatChannelManager
from server.domain.auth import AuthToken
from server.infrastructure.config import Settings, load_settings
from server.infrastructure.http_proxy_gateway import HttpProxyGateway
from server.infrastructure.session import SessionCodec


def current_workspace() -> Path:
    return Path(os.environ.get("SUPER_PERSONAL_WORKSPACE", Path.cwd())).resolve()


def create_container(settings: Settings, workspace: Path | None = None) -> AppContainer:
    project_root = Path(__file__).resolve().parents[2]
    active_workspace = workspace or current_workspace()
    system_log_service = SystemLogService(active_workspace)
    agent_chat_service = AgentChatService(active_workspace / "config.yaml")
    chat_session_service = ChatSessionService(active_workspace)
    agent_run_state_service = AgentRunStateService(active_workspace)
    critique_service = CritiqueService(active_workspace, agent_chat_service)
    wechat_channel_manager = WechatChannelManager(
        workspace=active_workspace,
        agent_chat_service=agent_chat_service,
        system_log_service=system_log_service,
        chat_session_service=chat_session_service,
    )
    return AppContainer(
        auth_service=AuthService(AuthToken(settings.auth.token)),
        config_file_service=ConfigFileService(active_workspace),
        proxy_service=ProxyService(HttpProxyGateway(settings.proxy.upstream_base_url)),
        system_log_service=system_log_service,
        system_update_service=SystemUpdateService(
            project_root,
            active_workspace,
            system_log_service,
        ),
        session_codec=SessionCodec(settings.auth.token),
        agent_chat_service=agent_chat_service,
        chat_session_service=chat_session_service,
        agent_run_state_service=agent_run_state_service,
        portfolio_service=PortfolioService(active_workspace),
        wechat_channel_manager=wechat_channel_manager,
        critique_service=critique_service,
    )


def install_request_logging(app: FastAPI, container: AppContainer) -> None:
    @app.middleware("http")
    async def log_api_request(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            client = request.client.host if request.client else "-"
            container.system_log_service.append_request_log(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                client=client,
            )


def create_app(settings: Settings | None = None, workspace: Path | None = None) -> FastAPI:
    if settings is None:
        active_workspace = workspace or current_workspace()
        settings = load_settings(active_workspace / "config.yaml")

    container = create_container(settings, workspace)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container.wechat_channel_manager is not None:
            await container.wechat_channel_manager.auto_start_all()
        try:
            yield
        finally:
            if container.wechat_channel_manager is not None:
                await container.wechat_channel_manager.stop_all()

    app = FastAPI(title="Super Personal Platform", lifespan=lifespan)
    install_request_logging(app, container)
    app.include_router(create_auth_router(container))
    app.include_router(create_agent_router(container))
    app.include_router(create_proxy_router(container))
    app.include_router(create_system_router(container))
    project_root = Path(__file__).resolve().parents[2]
    app.include_router(create_channel_router(container))
    app.include_router(create_portfolio_router(container))
    app.include_router(create_session_router(container))
    app.include_router(create_critique_router(container))
    app.include_router(create_api_fallback_proxy_router(container))
    app.include_router(create_root_asset_proxy_router(container))

    mount_frontend(app, container, project_root / "web" / "dist")
    return app
