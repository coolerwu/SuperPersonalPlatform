from __future__ import annotations

import asyncio
import json
import stat
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from app.services.browser_tools import (
    BROWSER_TOOL_NAMES,
    BrowserSession,
    BrowserToolError,
    format_snapshot,
    is_public_ip,
    merge_storage_state,
    normalize_ref,
    normalize_tab_name,
    parse_snapshot,
    playwright_installed,
    register_browser_tools,
    validate_browser_url_syntax,
    validate_press_key,
    validate_public_url,
    verification_reason,
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


def test_verification_reason_detects_captcha_pages() -> None:
    assert verification_reason(
        "https://wappass.baidu.com/static/captcha/tuxing.html?ak=1",
        "百度安全验证",
    )
    assert verification_reason(
        "https://www.google.com/sorry/index?continue=x",
        "Google",
    )
    assert verification_reason(
        "https://example.com/verify",
        "Just a moment...",
        total_elements=3,
        body_text="Checking your browser",
    )
    assert verification_reason(
        "https://example.com",
        "Example Domain",
        total_elements=1,
        body_text="This domain is for use in examples.",
    ) is None
    assert verification_reason(
        "https://zh.wikipedia.org/wiki/验证码",
        "验证码 - 维基百科",
        total_elements=80,
        body_text="x" * 5_000,
    ) is None


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
        self.url = "https://example.com/"

    async def close(self) -> None:
        self.closed = True

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    async def evaluate(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def screenshot(self, **_kwargs: Any) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + b"screenshot-bytes"


class FakeContext:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []
        self.storage_state_value: dict[str, Any] = {"cookies": [], "origins": []}

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

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def storage_state(self) -> dict[str, Any]:
        return self.storage_state_value

    async def close(self) -> None:
        return None


class FakeBrowser:
    version = "141.0.0.0"

    def __init__(self) -> None:
        self.context_kwargs: dict[str, Any] = {}
        self.context = FakeContext()

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_kwargs = kwargs
        return self.context

    async def close(self) -> None:
        return None


class FakeChromium:
    def __init__(self) -> None:
        self.launch_kwargs: dict[str, Any] = {}
        self.browser = FakeBrowser()

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()

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


async def test_screenshot_returns_image_content_block() -> None:
    import base64

    async def start() -> FakePlaywright:
        return FakePlaywright()

    session = BrowserSession(concurrency=1, start_playwright=start)
    registry = SkillToolRegistry()
    register_browser_tools(registry)
    try:
        await session._get_or_create_tab("main", create=True)
        tool = registry.resolve_names(
            ["browser_screenshot"],
            SkillToolContext(api_key="", base_url="", browser=session),
        )[0]
        blocks = await tool.ainvoke({"tab": "main"})
    finally:
        await session.aclose()

    assert blocks[0]["type"] == "text"
    assert "Screenshot of tab 'main'" in blocks[0]["text"]
    assert blocks[1]["type"] == "image_url"
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG\r\n\x1a\n")


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


def test_merge_storage_state_prefers_incoming_entries() -> None:
    existing = {
        "cookies": [
            {"name": "a", "value": "old", "domain": ".x.com", "path": "/"},
            {"name": "b", "value": "keep", "domain": ".x.com", "path": "/"},
        ],
        "origins": [
            {
                "origin": "https://x.com",
                "localStorage": [
                    {"name": "token", "value": "old"},
                    {"name": "keep", "value": "1"},
                ],
            }
        ],
    }
    incoming = {
        "cookies": [{"name": "a", "value": "new", "domain": ".x.com", "path": "/"}],
        "origins": [
            {
                "origin": "https://x.com",
                "localStorage": [{"name": "token", "value": "new"}],
            }
        ],
    }

    merged = merge_storage_state(existing, incoming)

    assert {(cookie["name"], cookie["value"]) for cookie in merged["cookies"]} == {
        ("a", "new"),
        ("b", "keep"),
    }
    values = {
        item["name"]: item["value"] for item in merged["origins"][0]["localStorage"]
    }
    assert values == {"token": "new", "keep": "1"}


async def test_normal_identity_and_persisted_login_state(tmp_path) -> None:
    state_path = tmp_path / "browser_state.json"
    playwright = FakePlaywright()

    async def start() -> FakePlaywright:
        return playwright

    session = BrowserSession(
        concurrency=1, state_path=state_path, start_playwright=start
    )
    try:
        await session._get_or_create_tab("main", create=True)
        launch_kwargs = playwright.chromium.launch_kwargs
        context_kwargs = playwright.chromium.browser.context_kwargs
        init_scripts = playwright.chromium.browser.context.init_scripts

        assert "--disable-blink-features=AutomationControlled" in launch_kwargs["args"]
        assert "HeadlessChrome" not in context_kwargs["user_agent"]
        assert "Chrome/141.0.0.0" in context_kwargs["user_agent"]
        assert context_kwargs["locale"] == "zh-CN"
        assert context_kwargs["timezone_id"] == "Asia/Shanghai"
        assert context_kwargs["extra_http_headers"]["Accept-Language"].startswith("zh-CN")
        assert any("webdriver" in script for script in init_scripts)
        assert "storage_state" not in context_kwargs

        playwright.chromium.browser.context.storage_state_value = {
            "cookies": [
                {
                    "name": "web_session",
                    "value": "abc",
                    "domain": ".xiaohongshu.com",
                    "path": "/",
                }
            ],
            "origins": [],
        }
    finally:
        await session.aclose()

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["cookies"][0]["name"] == "web_session"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600

    second = FakePlaywright()

    async def start_second() -> FakePlaywright:
        return second

    session2 = BrowserSession(
        concurrency=1, state_path=state_path, start_playwright=start_second
    )
    try:
        await session2._get_or_create_tab("main", create=True)
        loaded = second.chromium.browser.context_kwargs["storage_state"]
        assert loaded["cookies"][0]["value"] == "abc"
    finally:
        await session2.aclose()


async def test_unusable_state_falls_back_to_clean_context(tmp_path) -> None:
    state_path = tmp_path / "browser_state.json"
    state_path.write_text(
        json.dumps({"cookies": [{"bad": "cookie"}], "origins": []}), encoding="utf-8"
    )

    class RejectingBrowser(FakeBrowser):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def new_context(self, **kwargs: Any) -> FakeContext:
            self.calls += 1
            if "storage_state" in kwargs:
                raise RuntimeError("invalid storage state")
            return await super().new_context(**kwargs)

    class RejectingChromium(FakeChromium):
        def __init__(self) -> None:
            super().__init__()
            self.browser = RejectingBrowser()

    class RejectingPlaywright(FakePlaywright):
        def __init__(self) -> None:
            super().__init__()
            self.chromium = RejectingChromium()

    playwright = RejectingPlaywright()

    async def start() -> FakePlaywright:
        return playwright

    session = BrowserSession(
        concurrency=1, state_path=state_path, start_playwright=start
    )
    try:
        await session._get_or_create_tab("main", create=True)
    finally:
        await session.aclose()

    assert playwright.chromium.browser.calls == 2
    assert "storage_state" not in playwright.chromium.browser.context_kwargs


async def test_snapshot_wait_seconds_is_clamped(tmp_path) -> None:
    recorded: list[int] = []

    class RecordingPage(FakePage):
        async def wait_for_timeout(self, ms: int) -> None:
            recorded.append(ms)

    class RecordingContext(FakeContext):
        async def new_page(self) -> FakePage:
            return RecordingPage()

    class RecordingBrowser(FakeBrowser):
        def __init__(self) -> None:
            super().__init__()
            self.context = RecordingContext()

    class RecordingChromium(FakeChromium):
        def __init__(self) -> None:
            super().__init__()
            self.browser = RecordingBrowser()

    class RecordingPlaywright(FakePlaywright):
        def __init__(self) -> None:
            super().__init__()
            self.chromium = RecordingChromium()

    playwright = RecordingPlaywright()

    async def start() -> FakePlaywright:
        return playwright

    session = BrowserSession(
        concurrency=1,
        state_path=tmp_path / "browser_state.json",
        start_playwright=start,
    )
    try:
        await session._get_or_create_tab("main", create=True)
        await session.snapshot("main", wait_seconds=999)
    finally:
        await session.aclose()

    assert recorded == [60_000]


async def test_chromium_launches_with_the_configured_proxy() -> None:
    playwright = FakePlaywright()

    async def start() -> FakePlaywright:
        return playwright

    session = BrowserSession(
        proxy_url="192.168.1.2:7893",
        proxy_bypass="localhost,127.0.0.1,.internal",
        start_playwright=start,
    )
    try:
        await session._ensure_started()
    finally:
        await session.aclose()

    proxy = playwright.chromium.launch_kwargs["proxy"]
    assert proxy["server"] == "http://192.168.1.2:7893"
    assert proxy["bypass"] == (
        "localhost,.localhost,127.0.0.1,.127.0.0.1,internal,.internal"
    )


def test_proxy_url_credentials_are_split_for_playwright() -> None:
    session = BrowserSession(proxy_url="http://user:p%40ss@proxy.local:8080")
    assert session._playwright_proxy() == {
        "server": "http://proxy.local:8080",
        "username": "user",
        "password": "p@ss",
    }


async def test_proxied_hosts_skip_poisoned_local_dns() -> None:
    async def blocked(host: str, port: int) -> list[str]:
        return ["2001::1"]

    session = BrowserSession(proxy_url="http://127.0.0.1:7890", resolver=blocked)
    await session._check_host("www.youtube.com", 443)
    assert "www.youtube.com" in session._checked_hosts


async def test_no_proxy_entries_still_validate_dns() -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.1"]

    session = BrowserSession(
        proxy_url="http://127.0.0.1:7890",
        proxy_bypass=".example.com",
        resolver=private,
    )
    with pytest.raises(BrowserToolError, match="private or reserved"):
        await session._check_host("www.example.com", 443)


async def test_without_a_proxy_dns_validation_is_unchanged() -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["10.0.0.1"]

    session = BrowserSession(proxy_url="", proxy_bypass="", resolver=private)
    assert session._playwright_proxy() is None
    with pytest.raises(BrowserToolError, match="private or reserved"):
        await session._check_host("www.youtube.com", 443)


def test_invalid_proxy_url_falls_back_to_direct() -> None:
    session = BrowserSession(proxy_url="http://proxy.local:99999")
    assert session._playwright_proxy() is None


def test_ipv6_proxy_server_keeps_brackets() -> None:
    session = BrowserSession(proxy_url="http://[2001:db8::1]:8080")
    assert session._playwright_proxy() == {"server": "http://[2001:db8::1]:8080"}
