from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
import yaml

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.browser_profile_service import BrowserAuthSessionNotFoundError, BrowserProfileInUseError
from server.app.system_log_service import InvalidLogFileError
from server.app.system_update_service import UpdateAlreadyRunningError
from server.domain.agent_config import AgentConfigError
from server.app.webdav_context_service import WebDAVContextService
from server.infrastructure.config import load_settings, parse_settings
from server.infrastructure.nutstore_webdav import NutstoreWebDAVClient


class LogReadRequest(BaseModel):
    name: str


class WebDAVTestRequest(BaseModel):
    content: str | None = None


class BrowserAuthStartRequest(BaseModel):
    agent_id: str
    url: str = ""


class BrowserAuthNavigateRequest(BaseModel):
    url: str


class BrowserAuthClickRequest(BaseModel):
    x: float
    y: float


class BrowserAuthTypeRequest(BaseModel):
    text: str


class BrowserAuthPressRequest(BaseModel):
    key: str


def create_system_router(container: AppContainer) -> APIRouter:
    def require_system_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(
        prefix="/api/system",
        tags=["system"],
        dependencies=[Depends(require_system_auth)],
    )

    @router.post("/logs/list")
    def list_logs() -> dict[str, list[dict[str, str | int]]]:
        return {
            "logs": [
                {
                    "name": log.name,
                    "path": log.path,
                    "size": log.size,
                    "modified_at": log.modified_at,
                }
                for log in container.system_log_service.list_logs()
            ]
        }

    @router.post("/logs/read")
    def read_log(payload: LogReadRequest) -> dict[str, str | int | bool]:
        try:
            log = container.system_log_service.read_log(payload.name)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="日志文件不存在",
            ) from exc
        except InvalidLogFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="日志文件名无效",
            ) from exc
        return {
            "name": log.name,
            "path": log.path,
            "size": log.size,
            "modified_at": log.modified_at,
            "content": log.content,
            "truncated": log.truncated,
        }

    @router.post("/update-service")
    def update_service() -> dict[str, str | bool]:
        try:
            log_path = container.system_update_service.start_update()
        except UpdateAlreadyRunningError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="更新任务已经在执行",
            ) from exc

        return {
            "ok": True,
            "message": "更新已开始",
            "log_path": str(log_path),
        }

    @router.post("/maintenance/preview")
    def preview_maintenance() -> dict[str, object]:
        return container.maintenance_service.preview()

    @router.post("/maintenance/run")
    def run_maintenance() -> dict[str, object]:
        result = container.maintenance_service.cleanup(dry_run=False)
        container.system_log_service.append_line(
            "maintenance_cleanup "
            f"status=ok retention_days={result['retention_days']} "
            f"items={len(result['items'])} bytes={result['summary']['bytes']}"
        )
        return result

    @router.get("/browser-profiles")
    def browser_profiles() -> dict[str, object]:
        try:
            settings = load_settings(container.config_file_service.config_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"配置无效：{exc}") from exc
        agents = [
            {
                "id": agent.id,
                "name": agent.name,
            }
            for agent in settings.agent_workspace.agents
        ]
        return {
            "agents": agents,
            "profiles": container.browser_profile_service.profiles([agent["id"] for agent in agents]),
        }

    @router.post("/browser-auth/sessions")
    async def start_browser_auth(payload: BrowserAuthStartRequest) -> dict[str, object]:
        try:
            settings = load_settings(container.config_file_service.config_path)
            settings.agent_workspace.get_agent(payload.agent_id)
            session = await container.browser_profile_service.start_session(
                agent_id=payload.agent_id,
                url=payload.url,
                proxy=settings.browser.proxy,
                timeout_ms=settings.browser.timeout_ms,
            )
        except BrowserProfileInUseError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except AgentConfigError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"session": session}

    @router.get("/browser-auth/sessions/{session_id}")
    async def get_browser_auth(session_id: str) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.get_session(session_id)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc

    @router.get("/browser-auth/sessions/{session_id}/screenshot")
    async def browser_auth_screenshot(session_id: str) -> Response:
        try:
            image = await container.browser_profile_service.screenshot(session_id)
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/browser-auth/sessions/{session_id}/navigate")
    async def browser_auth_navigate(session_id: str, payload: BrowserAuthNavigateRequest) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.navigate(session_id, payload.url)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/browser-auth/sessions/{session_id}/click")
    async def browser_auth_click(session_id: str, payload: BrowserAuthClickRequest) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.click(session_id, payload.x, payload.y)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/browser-auth/sessions/{session_id}/type")
    async def browser_auth_type(session_id: str, payload: BrowserAuthTypeRequest) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.type_text(session_id, payload.text)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/browser-auth/sessions/{session_id}/press")
    async def browser_auth_press(session_id: str, payload: BrowserAuthPressRequest) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.press_key(session_id, payload.key)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @router.post("/browser-auth/sessions/{session_id}/finish")
    async def browser_auth_finish(session_id: str) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.finish(session_id)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc

    @router.post("/browser-auth/sessions/{session_id}/cancel")
    async def browser_auth_cancel(session_id: str) -> dict[str, object]:
        try:
            return {"session": await container.browser_profile_service.cancel(session_id)}
        except BrowserAuthSessionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="浏览器授权会话不存在") from exc

    @router.post("/webdav-context/sync")
    async def sync_webdav_context() -> dict[str, object]:
        try:
            settings = load_settings(container.config_file_service.config_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"配置无效：{exc}",
            ) from exc

        if not settings.nutstore.enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="坚果云 WebDAV 未启用")
        if not settings.context.webdav_sync.enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Context WebDAV 同步未启用")
        if not settings.context.webdav_permissions:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少 WebDAV 权限规则")

        service = WebDAVContextService(
            workspace=container.workspace,
            nutstore=settings.nutstore,
            context=settings.context,
        )
        try:
            await service.refresh()
        except Exception as exc:  # noqa: BLE001
            container.system_log_service.append_line(
                f"webdav_context_sync_manual status=failed type={exc.__class__.__name__} message={exc}"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"WebDAV 同步失败：{exc}",
            ) from exc

        summary = service.summary()
        container.system_log_service.append_line(
            "webdav_context_sync_manual "
            f"status=ok documents={summary['documents']} assets={summary['assets']} total={summary['total']}"
        )
        return {
            "ok": True,
            "message": f"WebDAV 已同步：{summary['documents']} 个文本，{summary['assets']} 个图片资源",
            "summary": summary,
        }

    @router.post("/webdav-context/test")
    async def test_webdav_context(payload: WebDAVTestRequest) -> dict[str, object]:
        try:
            if payload.content is None:
                settings = load_settings(container.config_file_service.config_path)
                source = "saved"
            else:
                raw = yaml.safe_load(payload.content) or {}
                if not isinstance(raw, dict):
                    raise ValueError("config.yaml 顶层必须是对象")
                settings = parse_settings(raw)
                source = "draft"
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"配置无效：{exc}") from exc

        if not settings.nutstore.enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="坚果云 WebDAV 未启用")
        if not settings.nutstore.username or not settings.nutstore.password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="坚果云账号或应用密码为空")

        client = NutstoreWebDAVClient(settings.nutstore)
        try:
            result = await client.probe_list(settings.context.webdav_sync.root_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"WebDAV 连接测试失败：{exc}") from exc

        message = "WebDAV 连接成功" if result["ok"] else f"WebDAV 连接失败：HTTP {result['status_code']}"
        return {
            "ok": result["ok"],
            "message": message,
            "source": source,
            "target_url": result["target_url"],
            "status_code": result["status_code"],
        }

    return router
