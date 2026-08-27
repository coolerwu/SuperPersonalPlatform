import os
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from server.adapter.auth_routes import create_auth_router
from server.adapter.channel_routes import create_channel_router
from server.adapter.dependencies import AppContainer
from server.adapter.run_routes import create_run_router
from server.adapter.schedule_routes import create_schedule_router
from server.adapter.static_routes import mount_frontend
from server.adapter.system_routes import create_system_router
from server.adapter.workspace_routes import create_workspace_router
from server.app.auth_service import AuthService
from server.app.browser_profile_service import BrowserProfileService
from server.app.config_file_service import ConfigFileService
from server.app.maintenance_service import MaintenanceService
from server.app.nutstore_service import NutstoreService
from server.app.run_service import RunService
from server.app.schedule_service import ScheduleService
from server.app.session_service import SessionService
from server.app.system_log_service import SystemLogService
from server.app.system_update_service import SystemUpdateService
from server.app.wechat_channel_manager import WechatChannelManager
from server.app.workspace_file_service import WorkspaceFileService
from server.app.webdav_context_service import WebDAVContextService
from server.domain.auth import AuthToken
from server.infrastructure.config import Settings, load_settings
from server.infrastructure.session import SessionCodec


def current_workspace() -> Path:
    return Path(os.environ.get("SUPER_PERSONAL_WORKSPACE", Path.cwd())).resolve()


def create_container(settings: Settings, workspace: Path | None = None) -> AppContainer:
    project_root = Path(__file__).resolve().parents[2]
    active_workspace = workspace or current_workspace()
    system_log_service = SystemLogService(active_workspace)
    session_service = SessionService(active_workspace)
    run_service = RunService(active_workspace, session_service=session_service)
    wechat_channel_manager = WechatChannelManager(
        workspace=active_workspace,
        run_service=run_service,
        session_service=session_service,
        system_log_service=system_log_service,
    )
    webdav_context_service = None
    if settings.nutstore.enabled and settings.context.webdav_sync.enabled:
        webdav_context_service = WebDAVContextService(
            workspace=active_workspace,
            nutstore=settings.nutstore,
            context=settings.context,
        )
    maintenance_service = MaintenanceService(active_workspace, settings.maintenance)
    schedule_service = ScheduleService(
        workspace=active_workspace,
        settings=settings,
        run_service=run_service,
        system_log_service=system_log_service,
        maintenance_service=maintenance_service,
        webdav_context_service=webdav_context_service,
        channel_delivery_service=wechat_channel_manager,
    )
    run_service.set_schedule_service(schedule_service)
    return AppContainer(
        workspace=active_workspace,
        auth_service=AuthService(AuthToken(settings.auth.token)),
        browser_profile_service=BrowserProfileService(active_workspace),
        config_file_service=ConfigFileService(active_workspace),
        run_service=run_service,
        maintenance_service=maintenance_service,
        nutstore_service=NutstoreService(settings.nutstore),
        schedule_service=schedule_service,
        system_log_service=system_log_service,
        system_update_service=SystemUpdateService(
            project_root,
            active_workspace,
            system_log_service,
        ),
        workspace_file_service=WorkspaceFileService(active_workspace),
        session_codec=SessionCodec(settings.auth.token),
        wechat_channel_manager=wechat_channel_manager,
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
    active_workspace = workspace or current_workspace()
    if settings is None:
        settings = load_settings(active_workspace / "config.yaml")

    container = create_container(settings, active_workspace)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        schedule_task: asyncio.Task | None = None
        schedule_stop = asyncio.Event()
        schedule_task = asyncio.create_task(container.schedule_service.run_forever(schedule_stop))
        if container.wechat_channel_manager is not None:
            await container.wechat_channel_manager.auto_start_all()
        try:
            yield
        finally:
            if schedule_task is not None:
                schedule_stop.set()
                await schedule_task
            if container.wechat_channel_manager is not None:
                await container.wechat_channel_manager.stop_all()
            await container.browser_profile_service.close_all()

    app = FastAPI(title="Super Personal Platform", lifespan=lifespan)
    install_request_logging(app, container)
    app.include_router(create_auth_router(container))
    app.include_router(create_run_router(container))
    app.include_router(create_schedule_router(container))
    app.include_router(create_channel_router(container))
    app.include_router(create_system_router(container))
    app.include_router(create_workspace_router(container))

    project_root = Path(__file__).resolve().parents[2]
    mount_frontend(app, container, project_root / "web" / "dist")
    return app
