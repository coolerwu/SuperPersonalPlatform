from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request, Response, status

from server.adapter.dependencies import AppContainer
from server.domain.errors import InvalidTokenError


SESSION_COOKIE = "spp_session"


class LoginRequest(BaseModel):
    token: str


def create_auth_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login")
    def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
        try:
            container.auth_service.login(payload.token)
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            ) from exc

        response.set_cookie(
            SESSION_COOKIE,
            container.session_codec.issue(),
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
            "authenticated": container.session_codec.verify(
                request.cookies.get(SESSION_COOKIE)
            )
        }

    return router
