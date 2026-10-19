from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from langchain_core.tools import BaseTool, StructuredTool

if TYPE_CHECKING:
    from app.services.browser_tools import BrowserSession

# These tools are always exposed by deepagents; a skill tool with the same name
# would be treated as already visible and never gated.
RESERVED_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute", "task"}
)

FETCH_ENDPOINT_PATH = "/v1/tools/fetch"
SEARCH_ENDPOINT_PATH = "/v1/tools/search"
MAX_TOOL_NAME_LENGTH = 64
MAX_FETCH_URL_LENGTH = 8192
MAX_SEARCH_QUERY_LENGTH = 128
MAX_SEARCH_COUNT = 50
DEFAULT_SEARCH_COUNT = 10
MAX_TOOL_RESULT_CHARS = 200_000
TOOL_TIMEOUT_SECONDS = 30.0


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
    browser: BrowserSession | None = None


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


def validate_search_query(query: str) -> str:
    value = query.strip()
    if not value or len(value) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(
            f"query must be non-empty and at most {MAX_SEARCH_QUERY_LENGTH} characters"
        )
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


async def _post_tool(
    context: SkillToolContext, path: str, payload: dict[str, Any]
) -> dict[str, Any] | str:
    """Call one DeepSeek tools endpoint, returning its JSON object or an error string."""
    if not context.api_key:
        return "Error: provider api_key is not configured"

    owns_client = context.http_client is None
    client = context.http_client or httpx.AsyncClient(timeout=TOOL_TIMEOUT_SECONDS)
    try:
        response = await client.post(
            tool_endpoint(context.base_url, path),
            headers={
                "Authorization": f"Bearer {context.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.HTTPError as exc:
        return f"Error: request failed: {exc}"
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        return f"Error (HTTP {response.status_code}): {_error_message(response)}"

    try:
        data = response.json()
    except ValueError:
        return "Error: response was not valid JSON"
    if not isinstance(data, dict):
        return "Error: response was not an object"
    return data


async def _fetch_page(context: SkillToolContext, url: str) -> str:
    try:
        target = validate_fetch_url(url)
    except ValueError as exc:
        return f"Error: {exc}"

    data = await _post_tool(context, FETCH_ENDPOINT_PATH, {"url": target})
    if isinstance(data, str):
        return data

    title = str(data.get("title") or "")
    content = str(data.get("content") or "")
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = f"{content[:MAX_TOOL_RESULT_CHARS]}\n\n... [content truncated]"
    return f"# {title or target}\n\nURL: {target}\n\n{content}".strip()


def _format_search_results(
    query: str, results: list[Any], *, need_content: bool
) -> str:
    if not results:
        return f'没有找到与“{query}”相关的结果。'

    lines = [f'## 搜索结果：{query}', ""]
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "(untitled)")
        url = str(result.get("url") or "")
        summary = str(result.get("summary") or result.get("snippet") or "")
        meta = [
            str(result.get(key))
            for key in ("site_name", "date")
            if result.get(key)
        ]
        score = result.get("rerank_score")
        authority = result.get("authority")
        if score is not None:
            meta.append(f"rerank={score}")
        if authority is not None:
            meta.append(f"authority={authority}")
        lines.append(f"{index}. **{title}**")
        if url:
            lines.append(f"   URL: {url}")
        if summary:
            lines.append(f"   {summary}")
        if meta:
            lines.append(f"   {' · '.join(meta)}")
        if need_content:
            content = str(result.get("content") or "")
            if content:
                lines.append(f"   正文：{content}")
        lines.append("")

    output = "\n".join(lines).strip()
    if len(output) > MAX_TOOL_RESULT_CHARS:
        output = f"{output[:MAX_TOOL_RESULT_CHARS]}\n\n... [results truncated]"
    return output


async def _search_web(
    context: SkillToolContext, query: str, count: int, need_content: bool
) -> str:
    try:
        target = validate_search_query(query)
    except ValueError as exc:
        return f"Error: {exc}"
    if not 1 <= count <= MAX_SEARCH_COUNT:
        return f"Error: count must be between 1 and {MAX_SEARCH_COUNT}"

    data = await _post_tool(
        context,
        SEARCH_ENDPOINT_PATH,
        {"query": target, "count": count, "need_content": need_content},
    )
    if isinstance(data, str):
        return data

    results = data.get("results")
    if not isinstance(results, list):
        return "Error: search response did not contain a results array"
    return _format_search_results(target, results, need_content=need_content)


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


def _make_search_tool(context: SkillToolContext) -> BaseTool:
    async def web_search(query: str, count: int = DEFAULT_SEARCH_COUNT, need_content: bool = False) -> str:
        """搜索网页，返回标题、链接、摘要和来源信息。"""
        return await _search_web(context, query, count, need_content)

    return StructuredTool.from_function(
        name="web_search",
        description=(
            "搜索网页（DeepSeek Search API），返回标题、链接、摘要、来源和权威性评分。"
            "适合查最新信息、新闻、事实核验和寻找资料；query 为 1-128 字符，count 为 1-50。"
            "需要正文时把 need_content 设为 true，或再用 web_fetch 抓取具体链接。"
        ),
        coroutine=web_search,
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

    def has(self, name: str) -> bool:
        return name in self._specs

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
SKILL_TOOL_REGISTRY.register_factory(
    "web_search",
    _make_search_tool,
    description="搜索网页，返回标题、链接、摘要和来源信息（DeepSeek Search API）",
)
