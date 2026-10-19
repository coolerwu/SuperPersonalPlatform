from __future__ import annotations

import json

import httpx

from app.services.skill_tools import (
    SkillToolContext,
    SkillToolRegistry,
    _make_search_tool,
)


def _setup(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = SkillToolRegistry()
    registry.register_factory("web_search", _make_search_tool, description="web search")
    context = SkillToolContext(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        http_client=client,
    )
    tool = registry.resolve_names(["web_search"], context)[0]
    return tool, client


async def test_web_search_calls_deepseek_search_api() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "rerank_score": 2.4,
                        "title": "DeepSeek V4",
                        "url": "https://example.com/v4",
                        "snippet": "Short summary",
                        "summary": "Long summary",
                        "content": "Full body",
                        "site_name": "Example",
                        "date": "2026-06-01T00:00:00+00:00",
                        "authority": 3,
                    }
                ]
            },
        )

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"query": "deepseek", "count": 5})
    finally:
        await client.aclose()

    assert seen["url"] == "https://api.deepseek.com/v1/tools/search"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"] == {"query": "deepseek", "count": 5, "need_content": False}
    assert "DeepSeek V4" in result
    assert "https://example.com/v4" in result
    assert "Long summary" in result
    assert "authority=3" in result


async def test_web_search_includes_content_when_requested() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Title",
                        "url": "https://example.com",
                        "summary": "Summary",
                        "content": "Body text",
                    }
                ]
            },
        )

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke(
            {"query": "example", "count": 1, "need_content": True}
        )
    finally:
        await client.aclose()

    assert "正文：Body text" in result


async def test_web_search_handles_empty_results() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"query": "nothing"})
    finally:
        await client.aclose()

    assert result == '没有找到与“nothing”相关的结果。'


async def test_web_search_validates_arguments() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("invalid arguments must not reach the network")

    tool, client = _setup(handler)
    try:
        empty = await tool.ainvoke({"query": "   "})
        too_long = await tool.ainvoke({"query": "x" * 129})
        bad_count = await tool.ainvoke({"query": "ok", "count": 51})
    finally:
        await client.aclose()

    assert empty.startswith("Error: query must be non-empty")
    assert too_long.startswith("Error: query must be non-empty")
    assert bad_count == "Error: count must be between 1 and 50"


async def test_web_search_reports_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limited", "type": "rate_limit_error"}},
        )

    tool, client = _setup(handler)
    try:
        result = await tool.ainvoke({"query": "deepseek"})
    finally:
        await client.aclose()

    assert result.startswith("Error (HTTP 429)")
    assert "rate limited" in result
