import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings
from app.schemas import LoginRequest

COOKIE_NAME = "acc_auth"


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME) or ""
    return bool(token) and secrets.compare_digest(token, settings.app_auth_token)


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="authentication required")


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict[str, bool]:
    if not secrets.compare_digest(payload.token, settings.app_auth_token):
        raise HTTPException(status_code=401, detail="invalid token")
    response.set_cookie(
        COOKIE_NAME,
        settings.app_auth_token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}


@router.get("/me")
async def me(request: Request) -> dict[str, bool]:
    return {"authenticated": is_authenticated(request)}
