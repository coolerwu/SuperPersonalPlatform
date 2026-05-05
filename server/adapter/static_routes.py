from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server.adapter.auth_routes import SESSION_COOKIE
from server.adapter.dependencies import AppContainer


def mount_frontend(app, container: AppContainer, dist_dir: Path) -> None:
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str, request: Request):
        if path.startswith("api/"):
            return HTMLResponse("Not found", status_code=404)

        index_file = dist_dir / "index.html"
        if not index_file.exists():
            return HTMLResponse(
                "<h1>Frontend build not found</h1><p>Run cd web && npm run build.</p>",
                status_code=503,
            )

        is_login_path = path == "login"
        is_authenticated = container.session_codec.verify(
            request.cookies.get(SESSION_COOKIE)
        )
        if not is_login_path and not is_authenticated:
            return RedirectResponse("/login", status_code=303)
        if is_login_path and is_authenticated:
            return RedirectResponse("/", status_code=303)

        return FileResponse(index_file)
