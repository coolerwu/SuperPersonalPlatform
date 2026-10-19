from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import BaseTool, StructuredTool

# These tools are always exposed by deepagents; a skill tool with the same name
# would be treated as already visible and never gated.
RESERVED_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute", "task"}
)

FETCH_ENDPOINT_PATH = "/v1/tools/fetch"
MAX_TOOL_NAME_LENGTH = 64
MAX_FETCH_URL_LENGTH = 8192
MAX_FETCH_CONTENT_CHARS = 200_000
FETCH_TIMEOUT_SECONDS = 30.0


def is_valid_tool_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_TOOL_NAME_LENGTH
        and not any(char.isspace() or not char.isprintable() for char in name)
    )


def normalize_tool_names(names: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not is_valid_tool_name(name):
            raise ValueError(f"invalid tool name: {raw!r}")
        if name not in normalized:
            normalized.append(name)
    return normalized


@dataclass
class SkillToolContext:
    """Per-run credentials and transport a skill tool is built with."""

    api_key: str
    base_url: str
    http_client: httpx.AsyncClient | None = None


@dataclass(frozen=True)
class SkillToolSpec:
    name: str
    description: str
    factory: Callable[[SkillToolContext], BaseTool]


def tool_endpoint(base_url: str, path: str = FETCH_ENDPOINT_PATH) -> str:
    """Join a provider base URL with an API path without duplicating `/v1`."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}{path.removeprefix('/v1')}"
    return f"{base}{path}"


def validate_fetch_url(url: str) -> str:
    value = url.strip()
    if not value or len(value) > MAX_FETCH_URL_LENGTH:
        raise ValueError(f"url must be non-empty and at most {MAX_FETCH_URL_LENGTH} characters")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an HTTP/HTTPS URL with a host")
    if parts.username or parts.password:
        raise ValueError("url must not contain a username or password")
    return value


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500] or response.reason_phrase
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return response.text.strip()[:500] or response.reason_phrase


async def _fetch_page(context: SkillToolContext, url: str) -> str:
    try:
        target = validate_fetch_url(url)
    except ValueError as exc:
        return f"Error: {exc}"
    if not context.api_key:
        return "Error: provider api_key is not configured"

    owns_client = context.http_client is None
    client = context.http_client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            tool_endpoint(context.base_url),
            headers={
                "Authorization": f"Bearer {context.api_key}",
                "Content-Type": "application/json",
            },
            json={"url": target},
        )
    except httpx.HTTPError as exc:
        return f"Error: fetch request failed: {exc}"
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        return f"Error (HTTP {response.status_code}): {_error_message(response)}"

    try:
        payload = response.json()
    except ValueError:
        return "Error: fetch response was not valid JSON"
    if not isinstance(payload, dict):
        return "Error: fetch response was not an object"

    title = str(payload.get("title") or "")
    content = str(payload.get("content") or "")
    if len(content) > MAX_FETCH_CONTENT_CHARS:
        content = f"{content[:MAX_FETCH_CONTENT_CHARS]}\n\n... [content truncated]"
    return f"# {title or target}\n\nURL: {target}\n\n{content}".strip()


def _make_fetch_tool(context: SkillToolContext) -> BaseTool:
    async def web_fetch(url: str) -> str:
        """抓取一个网页，返回页面标题和 Markdown 正文。"""
        return await _fetch_page(context, url)

    return StructuredTool.from_function(
        name="web_fetch",
        description=(
            "抓取网页并以 Markdown 返回标题和正文（DeepSeek Fetch API）。"
            "用于阅读文章、文档或搜索结果中的链接；url 必须是完整的 http/https 地址。"
        ),
        coroutine=web_fetch,
    )


class SkillToolRegistry:
    """Tools a skill can unlock, built with the current Agent's provider."""

    def __init__(self, tools: Iterable[BaseTool] = ()) -> None:
        self._specs: dict[str, SkillToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        self._register(
            SkillToolSpec(
                name=tool.name,
                description=str(tool.description or ""),
                factory=lambda _context, bound=tool: bound,
            )
        )

    def register_factory(
        self,
        name: str,
        factory: Callable[[SkillToolContext], BaseTool],
        *,
        description: str = "",
    ) -> None:
        self._register(SkillToolSpec(name=name, description=description, factory=factory))

    def _register(self, spec: SkillToolSpec) -> None:
        name = spec.name
        if not is_valid_tool_name(name):
            raise ValueError(f"invalid skill tool name: {name!r}")
        if name in RESERVED_TOOL_NAMES:
            raise ValueError(f"skill tool name {name!r} is reserved")
        if name in self._specs:
            raise ValueError(f"duplicate skill tool name: {name!r}")
        self._specs[name] = spec

    def specs(self) -> list[SkillToolSpec]:
        return list(self._specs.values())

    def build(self, context: SkillToolContext) -> list[BaseTool]:
        return [spec.factory(context) for spec in self._specs.values()]

    def resolve_names(self, names: Iterable[str], context: SkillToolContext) -> list[BaseTool]:
        wanted = set(names)
        return [
            spec.factory(context) for name, spec in self._specs.items() if name in wanted
        ]


SKILL_TOOL_REGISTRY = SkillToolRegistry()
SKILL_TOOL_REGISTRY.register_factory(
    "web_fetch",
    _make_fetch_tool,
    description="抓取网页并以 Markdown 返回标题和正文（DeepSeek Fetch API）",
)
