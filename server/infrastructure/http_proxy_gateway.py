import re
from typing import Mapping

import httpx

from server.domain.errors import UpstreamProxyError
from server.domain.proxy import ProxyRequest, ProxyResponse


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

REQUEST_HEADER_BLOCKLIST = HOP_BY_HOP_HEADERS | {"host", "content-length"}
RESPONSE_HEADER_ALLOWLIST = {
    "content-type",
    "cache-control",
    "expires",
    "last-modified",
    "etag",
    "location",
    "set-cookie",
}
REWRITABLE_CONTENT_TYPES = {
    "text/html",
    "text/css",
    "application/javascript",
    "text/javascript",
}
ROOT_RELATIVE_PREFIXES = (
    "api",
    "assets",
    "ds-assets",
    "fonts",
    "favicon.ico",
)


class HttpProxyGateway:
    def __init__(
        self,
        upstream_base_url: str,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = httpx.URL(upstream_base_url)
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        target_url = self._build_target_url(request.path, request.query_string)
        headers = self._filter_request_headers(request.headers)

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            ) as client:
                upstream_response = await client.request(
                    request.method,
                    target_url,
                    headers=headers,
                    content=request.body,
                )
        except httpx.HTTPError as exc:
            raise UpstreamProxyError(str(exc)) from exc

        response_headers = self._filter_response_headers(upstream_response.headers)
        body = upstream_response.content
        if self._is_rewritable_text(response_headers):
            body = self._rewrite_text_body(body)
            response_headers["content-length"] = str(len(body))

        return ProxyResponse(
            status_code=upstream_response.status_code,
            headers=response_headers,
            body=body,
        )

    def _build_target_url(self, path: str, query_string: bytes) -> httpx.URL:
        clean_path = path.lstrip("/")
        base_path = self._base_url.path
        if not base_path.endswith("/"):
            base_path = f"{base_path}/"
        target_path = f"{base_path}{clean_path}"
        return self._base_url.copy_with(
            path=target_path,
            query=query_string,
        )

    def _filter_request_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in REQUEST_HEADER_BLOCKLIST
        }

    def _filter_response_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        filtered: dict[str, str] = {}
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key in RESPONSE_HEADER_ALLOWLIST:
                filtered[lower_key] = self._rewrite_header_value(lower_key, value)
        return filtered

    def _rewrite_header_value(self, header_name: str, value: str) -> str:
        if header_name != "location":
            return value
        try:
            location = httpx.URL(value)
        except httpx.InvalidURL:
            return value
        if location.is_relative_url:
            path = value if value.startswith("/") else f"/{value}"
            return f"/api/proxy/site{path}"
        if (
            location.scheme == self._base_url.scheme
            and location.host == self._base_url.host
            and location.port == self._base_url.port
        ):
            path = location.raw_path.decode("utf-8")
            query = location.query.decode("utf-8")
            suffix = f"{path}?{query}" if query else path
            return f"/api/proxy/site{suffix}"
        return value

    def _is_rewritable_text(self, headers: dict[str, str]) -> bool:
        content_type = headers.get("content-type", "").split(";", 1)[0].strip()
        return content_type in REWRITABLE_CONTENT_TYPES

    def _rewrite_text_body(self, body: bytes) -> bytes:
        try:
            text = body.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = body.decode("latin-1")
            encoding = "latin-1"

        text = re.sub(
            r'(?P<attr>\b(?:href|src|action)=["\'])/(?!/)',
            r"\g<attr>/api/proxy/site/",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"url\(/(?!/)",
            "url(/api/proxy/site/",
            text,
            flags=re.IGNORECASE,
        )
        prefixes = "|".join(re.escape(prefix) for prefix in ROOT_RELATIVE_PREFIXES)
        text = re.sub(
            rf'(?P<quote>["\'`])/(?!api/proxy/site/)(?P<path>(?:{prefixes})(?:[/?#][^"\'`]*)?)(?P=quote)',
            r"\g<quote>/api/proxy/site/\g<path>\g<quote>",
            text,
        )
        return text.encode(encoding)
