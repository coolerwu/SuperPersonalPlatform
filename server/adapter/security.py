from fastapi import HTTPException, Request, status

from server.adapter.auth_routes import SESSION_COOKIE, is_authenticated_request
from server.adapter.dependencies import AppContainer


def require_authenticated(request: Request, container: AppContainer) -> None:
    if not is_authenticated_request(container, request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
