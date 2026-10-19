from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from app.services.browser_tools import (
    BROWSER_TOOL_NAMES,
    BrowserSession,
    BrowserToolError,
    format_snapshot,
    is_public_ip,
    normalize_ref,
    normalize_tab_name,
    parse_snapshot,
    playwright_installed,
    register_browser_tools,
    validate_browser_url_syntax,
    validate_press_key,
    validate_public_url,
)
from app.services.executor import browser_action_payload
from app.services.skill_tools import SkillToolContext, SkillToolRegistry


def test_is_public_ip_rejects_private_and_special_addresses() -> None:
    for address in (
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.3",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
    ):
        assert is_public_ip(address) is False, address
    assert is_public_ip("8.8.8.8") is True
    assert is_public_ip("2606:4700:4700::1111") is True


def test_validate_browser_url_syntax_rejects_unsafe_urls() -> None:
    for url in (
        "",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "https://user:pass@example.com/",
        "http://example.com:99999/",
    ):
        with pytest.raises(BrowserToolError):
            validate_browser_url_syntax(url)


def test_validate_browser_url_syntax_accepts_public_urls() -> None:
    assert validate_browser_url_syntax("https://example.com/path") == "https://example.com/path"
    assert validate_browser_url_syntax("http://8.8.8.8/") == "http://8.8.8.8/"


async def test_validate_public_url_uses_resolver() -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def private(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    async def mixed(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.1"]

    async def broken(host: str, port: int) -> list[str]:
        raise OSError("dns down")

    assert await validate_public_url("https://example.com/", resolver=public) == "https://example.com/"
    for resolver in (private, mixed, broken):
        with pytest.raises(BrowserToolError):
            await validate_public_url("https://example.com/", resolver=resolver)


async def test_validate_public_url_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.browser_tools as module

    monkeypatch.setattr(module, "DNS_TIMEOUT_SECONDS", 0.01)

    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(0.2)
        return ["93.184.216.34"]

    with pytest.raises(BrowserToolError, match="timeout"):
        await validate_public_url("https://example.com/", resolver=slow)


def test_normalize_ref() -> None:
    assert normalize_ref("s1e3") == "s1e3"
    assert normalize_ref("ref=s12e3") == "s12e3"
    assert normalize_ref("[s2e4]") == "s2e4"
    for value in ("e3", "s0e1", "s1e0", "a[href]", "s1e", ""):
        with pytest.raises(BrowserToolError):
            normalize_ref(value)


def test_normalize_tab_name_and_press_key() -> None:
    assert normalize_tab_name("") == "main"
    assert normalize_tab_name(None) == "main"
    assert normalize_tab_name(" results ") == "results"
    with pytest.raises(BrowserToolError):
        normalize_tab_name("bad tab")
    assert validate_press_key("Enter") == "Enter"
    assert validate_press_key("Control+A") == "Control+A"
    with pytest.raises(BrowserToolError):
        validate_press_key("Enter; rm -rf /")


def test_parse_and_format_snapshot() -> None:
    raw = {
        "title": "Example",
        "url": "https://example.com/",
        "totalElements": 3,
        "bodyText": "Hello world",
        "bodyTruncated": False,
        "elements": [
            {
                "ref": "s1e1",
                "tag": "a",
                "href": "https://example.com/more",
                "label": "More information...",
            },
            {
                "ref": "s1e2",
                "tag": "input",
                "type": "password",
                "name": "pw",
                "value": "secret",
                "label": "Password",
            },
            {
                "ref": "s1e3",
                "tag": "select",
                "name": "country",
                "value": "US",
                "options": 3,
                "label": "Country",
                "disabled": True,
            },
        ],
    }
    data = parse_snapshot(raw)
    output = format_snapshot(
        data, max_elements=10, max_text_chars=100, max_snapshot_chars=10_000
    )
    assert "Page: Example" in output
    assert "URL: https://example.com/" in output
    assert "Interactive elements: 3/3" in output
    assert '[ref=s1e1] link "More information..." -> https://example.com/more' in output
    assert "secret" not in output
    assert "(3 options)" in output
    assert "(disabled)" in output
    assert "--- page text ---" in output
    assert "Hello world" in output


def test_format_snapshot_reports_truncation() -> None:
    raw = {
        "title": "T",
        "url": "https://example.com/",
        "totalElements": 5,
        "bodyText": "x" * 20,
        "bodyTruncated": True,
        "elements": [
            {"ref": "s1e1", "tag": "button", "label": "One"},
            {"ref": "s1e2", "tag": "button", "label": "Two"},
        ],
    }
    output = format_snapshot(
        parse_snapshot(raw), max_elements=2, max_text_chars=10, max_snapshot_chars=10_000
    )
    assert "(showing first 2)" in output
    assert "... [3 more elements not shown]" in output
    assert "... [page text truncated]" in output

    capped = format_snapshot(
        parse_snapshot(raw), max_elements=2, max_text_chars=10, max_snapshot_chars=20
    )
    assert capped.endswith("... [snapshot truncated]")


def test_register_browser_tools_is_idempotent() -> None:
    registry = SkillToolRegistry()
    register_browser_tools(registry)
    assert {spec.name for spec in registry.specs()} == BROWSER_TOOL_NAMES
    register_browser_tools(registry)
    assert len(registry.specs()) == len(BROWSER_TOOL_NAMES)
    assert registry.has("browser_open") is True
    assert registry.has("missing") is False


async def test_tool_reports_missing_session() -> None:
    registry = SkillToolRegistry()
    register_browser_tools(registry)
    context = SkillToolContext(api_key="", base_url="", browser=None)
    tool = registry.resolve_names(["browser_snapshot"], context)[0]
    result = await tool.ainvoke({"tab": "main"})
    assert result == "Error: browser session is not available for this run"


async def test_missing_playwright_reports_install_hint() -> None:
    async def broken_start() -> Any:
        raise ImportError("playwright missing")

    session = BrowserSession(start_playwright=broken_start)
    with pytest.raises(BrowserToolError, match="Playwright is not installed"):
        await session._ensure_started()
    assert playwright_installed() in (True, False)


class FakePage:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


class FakeContext:
    def set_default_timeout(self, _timeout: int) -> None:
        return None

    def set_default_navigation_timeout(self, _timeout: int) -> None:
        return None

    async def route(self, _pattern: str, _handler: Any) -> None:
        return None

    def on(self, _event: str, _handler: Any) -> None:
        return None

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        return None


class FakeBrowser:
    async def new_context(self, **_kwargs: Any) -> FakeContext:
        return FakeContext()

    async def close(self) -> None:
        return None


class FakeChromium:
    async def launch(self, **_kwargs: Any) -> FakeBrowser:
        return FakeBrowser()


class FakePlaywright:
    chromium = FakeChromium()

    async def stop(self) -> None:
        return None


async def test_tab_limit_is_enforced_and_released() -> None:
    async def start() -> FakePlaywright:
        return FakePlaywright()

    session = BrowserSession(concurrency=2, start_playwright=start)
    await session._get_or_create_tab("a", create=True)
    await session._get_or_create_tab("b", create=True)
    with pytest.raises(BrowserToolError, match="tab limit reached"):
        await session._get_or_create_tab("c", create=True)

    result = await session.close_tab("a")
    assert "Closed tab 'a'" in result
    await session._get_or_create_tab("c", create=True)
    await session.aclose()


def test_browser_action_payload_is_compact() -> None:
    message = ToolMessage(
        content="Opened https://example.com (HTTP 200)\n\nPage: Example\n" + "x" * 500,
        tool_call_id="call-1",
        name="browser_open",
    )
    payload = browser_action_payload(("subagent",), "main", "browser_open", message)
    assert payload["agent"] == "subagent"
    assert payload["tool"] == "browser_open"
    assert payload["ok"] is True
    assert len(payload["summary"]) <= 240

    failure = ToolMessage(
        content="Error: private address", tool_call_id="call-2", name="browser_open"
    )
    assert browser_action_payload((), "main", "browser_open", failure)["ok"] is False
