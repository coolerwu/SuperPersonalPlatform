from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx

from server.domain.agent_config import AgentConfigError
from server.infrastructure.config import NutstoreConfig


@dataclass(frozen=True)
class WebDAVEntry:
    path: str
    name: str
    is_dir: bool
    size: int
    modified: str


class NutstoreWebDAVClient:
    def __init__(
        self,
        config: NutstoreConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._root_path = _normalize_path(config.root_path, allow_empty=True)
        self._base_url = config.base_url.rstrip("/") + "/"

    async def list(self, path: str = "") -> tuple[WebDAVEntry, ...]:
        self._ensure_enabled()
        target_path = self._resolve_path(path, directory=True)
        response = await self._request(
            "PROPFIND",
            target_path,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=(
                "<?xml version='1.0' encoding='utf-8'?>"
                "<propfind xmlns='DAV:'><prop>"
                "<resourcetype/><getcontentlength/><getlastmodified/>"
                "</prop></propfind>"
            ),
        )
        if response.status_code not in {207}:
            self._raise_response_error(response, "list")
        return self._parse_multistatus(response.text, target_path)

    async def read_bytes(self, path: str, *, max_bytes: int = 200000) -> tuple[bytes, bool]:
        self._ensure_enabled()
        if max_bytes <= 0:
            raise AgentConfigError("max_bytes must be greater than zero")
        response = await self._request("GET", self._resolve_path(path))
        if response.status_code != 200:
            self._raise_response_error(response, "read")
        content = response.content
        return content[:max_bytes], len(content) > max_bytes

    async def write_bytes(
        self,
        path: str,
        content: bytes,
        *,
        create_parent: bool = True,
    ) -> None:
        self._ensure_enabled()
        target_path = self._resolve_path(path)
        if create_parent:
            await self._ensure_parent_dirs(target_path)
        response = await self._request("PUT", target_path, content=content)
        if response.status_code not in {200, 201, 204}:
            self._raise_response_error(response, "write")

    async def delete(self, path: str) -> None:
        self._ensure_enabled()
        response = await self._request("DELETE", self._resolve_path(path))
        if response.status_code not in {200, 202, 204, 404}:
            self._raise_response_error(response, "delete")

    async def _ensure_parent_dirs(self, target_path: str) -> None:
        parent = PurePosixPath(target_path).parent
        if str(parent) in {"", "."}:
            return
        root = PurePosixPath(self._root_path)
        if parent == root:
            return
        try:
            relative_parent = parent.relative_to(root)
        except ValueError:
            relative_parent = parent
            current = PurePosixPath("/")
        else:
            current = root
        for part in relative_parent.parts:
            if part in {"", "/"}:
                continue
            current = current / part
            response = await self._request("MKCOL", str(current))
            if response.status_code not in {201, 405}:
                self._raise_response_error(response, "create directory")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        auth = (self._config.username, self._config.password)
        async with httpx.AsyncClient(
            auth=auth,
            follow_redirects=True,
            timeout=30,
            transport=self._transport,
            trust_env=False,
        ) as client:
            return await client.request(method, self._url_for_path(path), **kwargs)

    def _url_for_path(self, path: str) -> str:
        trailing_slash = path.endswith("/")
        normalized = _normalize_path(path, allow_empty=True)
        encoded = quote(normalized.lstrip("/"), safe="/")
        url = urljoin(self._base_url, encoded)
        if trailing_slash and not url.endswith("/"):
            url += "/"
        return url

    def _resolve_path(self, path: str, *, directory: bool = False) -> str:
        requested = _normalize_path(path, allow_empty=True)
        root = PurePosixPath(self._root_path)
        target = root
        if requested != "/":
            target = root / requested.lstrip("/")
        normalized = _normalize_path(str(target), allow_empty=True)
        if directory and not normalized.endswith("/"):
            normalized += "/"
        return normalized

    def _parse_multistatus(self, text: str, requested_path: str) -> tuple[WebDAVEntry, ...]:
        ns = {"d": "DAV:"}
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise AgentConfigError("nutstore WebDAV list response is not valid XML") from exc
        requested = requested_path.rstrip("/") or "/"
        entries: list[WebDAVEntry] = []
        for response in root.findall("d:response", ns):
            href = response.findtext("d:href", default="", namespaces=ns)
            path = self._path_from_href(href)
            if path.rstrip("/") == requested:
                continue
            prop = response.find("d:propstat/d:prop", ns)
            if prop is None:
                continue
            is_dir = prop.find("d:resourcetype/d:collection", ns) is not None
            size_text = prop.findtext("d:getcontentlength", default="0", namespaces=ns)
            modified = prop.findtext("d:getlastmodified", default="", namespaces=ns)
            entries.append(
                WebDAVEntry(
                    path=path,
                    name=PurePosixPath(path.rstrip("/")).name,
                    is_dir=is_dir,
                    size=int(size_text or 0),
                    modified=modified,
                )
            )
        return tuple(entries)

    def _path_from_href(self, href: str) -> str:
        parsed = urlparse(href)
        parsed_base = urlparse(self._base_url)
        base_path = parsed_base.path.rstrip("/")
        raw_path = unquote(parsed.path)
        if base_path and raw_path.startswith(base_path):
            raw_path = raw_path[len(base_path):]
        return _normalize_path(raw_path, allow_empty=True)

    def _ensure_enabled(self) -> None:
        if not self._config.enabled:
            raise AgentConfigError("nutstore is disabled")
        if not self._config.username or not self._config.password:
            raise AgentConfigError("nutstore username/password are required")

    def _raise_response_error(self, response: httpx.Response, action: str) -> None:
        raise AgentConfigError(
            f"nutstore WebDAV {action} failed: HTTP {response.status_code}"
        )


def _normalize_path(path: str, *, allow_empty: bool = False) -> str:
    value = str(path or "").strip()
    if not value:
        if allow_empty:
            return "/"
        raise AgentConfigError("path is required")
    parts = []
    for part in PurePosixPath("/" + value.lstrip("/")).parts:
        if part in {"", "/"}:
            continue
        if part in {".", ".."}:
            raise AgentConfigError("path must not contain . or ..")
        parts.append(part)
    return "/" + "/".join(parts)
