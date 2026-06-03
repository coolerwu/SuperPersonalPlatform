import os

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, Response, status

from server.adapter.dependencies import AppContainer
from server.domain.auth import AuthToken
from server.domain.errors import InvalidTokenError
from server.infrastructure.config import load_settings
from server.infrastructure.session import SessionCodec


SESSION_COOKIE = "spp_session"


class LoginRequest(BaseModel):
    token: str


def create_auth_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
        if is_dev_auth_bypass_enabled():
            return {"ok": True}

        session_codec = current_session_codec(container)
        try:
            AuthToken(session_codec.secret).verify(payload.token)
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            ) from exc

        response.set_cookie(
            SESSION_COOKIE,
            session_codec.issue(),
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
        return {"ok": True}

    @router.post("/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @router.get("/me")
    def me(request: Request) -> dict[str, bool]:
        return {
            "authenticated": is_authenticated_request(container, request.cookies.get(SESSION_COOKIE))
        }

    return router


def is_dev_auth_bypass_enabled() -> bool:
    enabled = os.environ.get("SUPER_PERSONAL_DEV_AUTH_BYPASS", "").lower()
    reload_enabled = os.environ.get("SUPER_PERSONAL_RELOAD", "").lower()
    return enabled in {"1", "true", "yes", "on"} and reload_enabled in {"1", "true", "yes", "on"}


def is_authenticated_request(container: AppContainer, session_cookie: str | None) -> bool:
    if is_dev_auth_bypass_enabled():
        return True
    return current_session_codec(container).verify(session_cookie)


def current_session_codec(container: AppContainer) -> SessionCodec:
    try:
        settings = load_settings(container.config_file_service.config_path)
    except Exception:
        return container.session_codec
    return SessionCodec(settings.auth.token)
