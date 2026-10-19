from __future__ import annotations

import httpx

from app.services.skill_tools import (
    SkillToolContext,
    SkillToolRegistry,
    _make_fetch_tool,
)


def _setup(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = SkillToolRegistry()
    registry.register_factory("web_fetch", _make_fetch_tool, description="web fetch")
    context = SkillToolContext(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        http_client=client,
    )
    tool = registry.resolve_names(["web_fetch"], context)[0]
    return tool, client


async def test_web_fetch_calls_deepseek_fetch_api() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"title": "Example", "content": "# Body"})

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"url": "https://example.com/article"})
    finally:
        await client.aclose()

    assert seen["url"] == "https://api.deepseek.com/v1/tools/fetch"
    assert seen["auth"] == "Bearer test-key"
    assert seen["content_type"].startswith("application/json")
    assert "Example" in result
    assert "# Body" in result
    assert "https://example.com/article" in result


async def test_web_fetch_reports_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"message": "page not found", "type": "invalid_request_error"}},
        )

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"url": "https://example.com/missing"})
    finally:
        await client.aclose()

    assert result.startswith("Error (HTTP 404)")
    assert "page not found" in result


async def test_web_fetch_rejects_invalid_urls() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("invalid URLs must not reach the network")

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"url": "ftp://example.com/file"})
        credentials = await tool.ainvoke({"url": "https://user:pass@example.com"})
    finally:
        await client.aclose()

    assert result.startswith("Error: url must be an HTTP/HTTPS URL")
    assert credentials.startswith("Error: url must not contain a username or password")


async def test_web_fetch_requires_api_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("missing key must not reach the network")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = _make_fetch_tool(
        SkillToolContext(api_key="", base_url="https://api.deepseek.com", http_client=client)
    )
    try:
        result = await tool.ainvoke({"url": "https://example.com"})
    finally:
        await client.aclose()

    assert result == "Error: provider api_key is not configured"
