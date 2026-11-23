from __future__ import annotations

import base64
import os

import pytest

from app.services.browser_service import BrowserService
from app.services.browser_tools import BrowserSession, BrowserToolError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="requires Chromium and network; set RUN_BROWSER_TESTS=1",
)


async def test_open_example_com_returns_text_snapshot() -> None:
    session = BrowserSession(concurrency=1)
    try:
        output = await session.open("https://example.com")
    finally:
        await session.aclose()

    assert "Page: Example Domain" in output
    assert "Interactive elements:" in output
    assert "[ref=" in output
    assert "--- page text ---" in output


async def test_screenshot_returns_png_image() -> None:
    session = BrowserSession(concurrency=1)
    try:
        await session.open("https://example.com")
        blocks = await session.screenshot("main")
    finally:
        await session.aclose()

    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image_url"
    raw = base64.b64decode(blocks[1]["image_url"]["url"].split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")


async def test_private_address_is_rejected_before_navigation() -> None:
    session = BrowserSession(concurrency=1)
    try:
        with pytest.raises(BrowserToolError):
            await session.open("http://127.0.0.1:8000")
    finally:
        await session.aclose()


async def test_service_keeps_session_tabs_isolated() -> None:
    service = BrowserService(
        session_factory=lambda: BrowserSession(concurrency=2), idle_seconds=300
    )
    await service.startup()
    try:
        async with service.use() as session:
            name_a = await service.resolve_tab(session, "aaaa-1")
            await session.open("https://example.com", name_a)
            name_b = await service.resolve_tab(session, "bbbb-2")
            await session.open("https://example.com", name_b)
            names = [tab["name"] for tab in await session.tabs_state()]
            assert "chat-aaaa-1" in names
            assert "chat-bbbb-2" in names
        rows = await service.list_tabs(session, "aaaa-1")
        assert [row["name"] for row in rows] == ["chat-aaaa-1"]
    finally:
        await service.shutdown()


async def test_service_new_tab_and_close() -> None:
    service = BrowserService(
        session_factory=lambda: BrowserSession(concurrency=2), idle_seconds=300
    )
    await service.startup()
    try:
        async with service.use() as session:
            info = await service.new_tab(
                session, "aaaa-1", url="https://example.com"
            )
            assert info["name"] == "chat-aaaa-1"
            second = await service.new_tab(session, "aaaa-1")
            assert second["name"] == "chat-aaaa-1-2"
            message = await service.close_tabs(session, "aaaa-1")
            # close_tabs 的消息只列出剩余标签页，关闭结果以计数表达。
            assert message == "Closed 2 tab(s). Open tabs: none."
            assert await service.list_tabs(session, "aaaa-1") == []
    finally:
        await service.shutdown()
