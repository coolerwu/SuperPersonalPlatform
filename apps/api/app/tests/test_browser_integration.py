from __future__ import annotations

import base64
import os

import pytest

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
