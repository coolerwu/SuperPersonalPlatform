from fastapi import APIRouter, HTTPException, Request, Response, status

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.domain.errors import UpstreamProxyError
from server.domain.proxy import ProxyRequest


PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def create_proxy_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/proxy", tags=["proxy"])

    async def proxy_site(request: Request, path: str = "") -> Response:
        require_authenticated(request, container)
        try:
            proxy_response = await container.proxy_service.forward(
                ProxyRequest(
                    method=request.method,
                    path=path,
                    query_string=request.scope.get("query_string", b""),
                    headers=dict(request.headers),
                    body=await request.body(),
                )
            )
        except UpstreamProxyError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to read upstream site",
            ) from exc

        return Response(
            content=proxy_response.body,
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
        )

    router.add_api_route(
        "/site/",
        proxy_site,
        methods=PROXY_METHODS,
        name="proxy_site_root",
    )
    router.add_api_route(
        "/site/{path:path}",
        proxy_site,
        methods=PROXY_METHODS,
        name="proxy_site_path",
    )
    return router


def create_api_fallback_proxy_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["proxy"])

    async def proxy_api_fallback(path: str, request: Request) -> Response:
        require_authenticated(request, container)
        try:
            proxy_response = await container.proxy_service.forward(
                ProxyRequest(
                    method=request.method,
                    path=f"api/{path}",
                    query_string=request.scope.get("query_string", b""),
                    headers=dict(request.headers),
                    body=await request.body(),
                )
            )
        except UpstreamProxyError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to read upstream site",
            ) from exc

        return Response(
            content=proxy_response.body,
            status_code=proxy_response.status_code,
            headers=proxy_response.headers,
        )

    router.add_api_route(
        "/{path:path}",
        proxy_api_fallback,
        methods=PROXY_METHODS,
        name="proxy_api_fallback",
    )
    return router


def create_root_asset_proxy_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["proxy"])

    def make_proxy_root_asset(prefix: str):
        async def proxy_root_asset(rest: str, request: Request) -> Response:
            require_authenticated(request, container)
            try:
                proxy_response = await container.proxy_service.forward(
                    ProxyRequest(
                        method=request.method,
                        path=f"{prefix}/{rest}",
                        query_string=request.scope.get("query_string", b""),
                        headers=dict(request.headers),
                        body=await request.body(),
                    )
                )
            except UpstreamProxyError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read upstream site",
                ) from exc

            return Response(
                content=proxy_response.body,
                status_code=proxy_response.status_code,
                headers=proxy_response.headers,
            )

        return proxy_root_asset

    for prefix in ("fonts", "ds-assets", "dashboard-plugins"):
        router.add_api_route(
            f"/{prefix}/{{rest:path}}",
            make_proxy_root_asset(prefix),
            methods=PROXY_METHODS,
            name=f"proxy_root_asset_{prefix}",
        )
    return router
