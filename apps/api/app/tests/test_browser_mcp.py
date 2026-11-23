from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import pytest

from app.services import browser_mcp
from app.services.browser_tools import BrowserToolError


class FakeSession:
    closed = False

    async def open(self, url: str, tab: str) -> str:
        return f"Opened {url} in {tab}"

    async def snapshot(self, tab: str) -> str:
        return f"SNAP {tab}"

    async def click(self, target: str, tab: str) -> str:
        return f"Clicked {target}"

    async def fill(self, target: str, text: str, tab: str, *, submit: bool = False) -> str:
        return f"Filled {target} submit={submit}"

    async def fill_many(self, fields: list[dict], tab: str) -> str:
        return f"Filled {len(fields)} field(s)"

    async def select_option(self, target: str, values: list[str], tab: str) -> str:
        return f"Selected {values} at {target}"

    async def press(self, key: str, tab: str) -> str:
        return f"Pressed {key}"

    async def wait_for(self, tab: str, *, text=None, text_gone=None, time_ms=None) -> str:
        if text is None and text_gone is None and time_ms is None:
            raise BrowserToolError("wait_for needs text, textGone, or time")
        return "Waited"

    async def navigate_back(self, tab: str) -> str:
        return "Went back"

    async def console_messages(self, tab: str) -> list[dict[str, str]]:
        return [{"type": "log", "text": "hello", "time": "2026-11-23T00:00:00+00:00"}]

    async def screenshot(self, tab: str, *, full_page: bool = False) -> list[dict]:
        data = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
        return [
            {"type": "text", "text": f"Screenshot of {tab}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        ]

    async def new_tab(self, name: str) -> str:
        return name

    async def tabs_state(self) -> list[dict]:
        return []

    async def close_tab(self, name: str) -> str:
        return f"Closed {name}"


class FakeService:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()
        self.reset_calls = 0
        self.select_calls: list = []
        self.close_calls: list = []
        self.new_calls: list = []

    @asynccontextmanager
    async def use(self):
        yield self.session

    async def reset(self) -> None:
        self.reset_calls += 1

    async def resolve_tab(self, session, session_id, tab=None) -> str:
        return "chat-abc-1"

    async def prepare_tab(self, session, session_id, tab=None) -> str:
        return "chat-abc-1"

    async def list_tabs(self, session, session_id):
        return [
            {"index": 1, "name": "chat-abc-1", "url": "https://example.com/", "default": True}
        ]

    async def new_tab(self, session, session_id, url=None):
        self.new_calls.append(url)
        return {"index": 2, "name": "chat-abc-1-2"}

    async def select_tab(self, session, session_id, tab=None):
        self.select_calls.append(tab)
        return "chat-abc-1"

    async def close_tabs(self, session, session_id, tab=None):
        self.close_calls.append(tab)
        return "Closed chat-abc-1. Open tabs: none."


def build_tools(tmp_path, service=None, session=None):
    return browser_mcp._build_tools(
        service or FakeService(session),
        agent_id="default",
        session_id="abc-1",
        workspace=tmp_path,
    )


def find(tools, name):
    return next(tool for tool in tools if tool.name == name)


@pytest.mark.asyncio
async def test_tool_names_are_stable(tmp_path) -> None:
    tools = build_tools(tmp_path)
    assert {tool.name for tool in tools} == set(browser_mcp.TOOL_NAMES)


@pytest.mark.asyncio
async def test_navigate_returns_snapshot_text(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "navigate").handler({"url": "https://example.com"})
    assert result["content"][0]["text"] == "Opened https://example.com in chat-abc-1"


@pytest.mark.asyncio
async def test_snapshot_returns_session_snapshot(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "snapshot").handler({})
    assert result["content"][0]["text"] == "SNAP chat-abc-1"


@pytest.mark.asyncio
async def test_browser_error_becomes_is_error(tmp_path) -> None:
    class Broken(FakeSession):
        async def click(self, target, tab):
            raise BrowserToolError("ref s1e3 is stale")

    service = FakeService(Broken())
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "click").handler({"target": "s1e3"})
    assert result["is_error"] is True
    assert result["content"][0]["text"] == "error: ref s1e3 is stale"
    assert service.reset_calls == 0


@pytest.mark.asyncio
async def test_dead_browser_error_resets_service(tmp_path) -> None:
    class Dead(FakeSession):
        async def click(self, target, tab):
            raise BrowserToolError(
                "Target page, context or browser has been closed"
            )

    service = FakeService(Dead())
    tools = browser_mcp._build_tools(
        service, agent_id="default", session_id="abc-1", workspace=tmp_path
    )
    result = await find(tools, "click").handler({"target": "s1e3"})
    assert result["is_error"] is True
    assert service.reset_calls == 1


@pytest.mark.asyncio
async def test_generic_dead_browser_error_resets_service(tmp_path) -> None:
    class GenericDead(FakeSession):
        async def click(self, target, tab):
            raise RuntimeError("Browser has been closed")

    service = FakeService(GenericDead())
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "click").handler({"target": "s1e3"})
    assert result["is_error"] is True
    assert service.reset_calls == 1


@pytest.mark.asyncio
async def test_network_error_does_not_reset_service(tmp_path) -> None:
    class NetworkDown(FakeSession):
        async def click(self, target, tab):
            raise BrowserToolError(
                "navigation failed: net::ERR_INTERNET_DISCONNECTED"
            )

    service = FakeService(NetworkDown())
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "click").handler({"target": "s1e3"})
    assert result["is_error"] is True
    assert service.reset_calls == 0


@pytest.mark.asyncio
async def test_wait_for_without_condition_is_error(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "wait_for").handler({})
    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_type_submits_when_requested(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "type").handler(
        {"target": "#q", "text": "hello", "submit": True}
    )
    assert "submit=True" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_type_does_not_submit_by_default(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "type").handler({"target": "#q", "text": "hello"})
    assert "submit=False" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_fill_form_passes_fields(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "fill_form").handler(
        {"fields": [{"target": "#a", "value": "1"}, {"target": "#b", "value": "2"}]}
    )
    assert result["content"][0]["text"] == "Filled 2 field(s)"


@pytest.mark.asyncio
async def test_fill_form_rejects_more_than_20_fields(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "fill_form").handler(
        {"fields": [{"target": f"#f{index}", "value": "x"} for index in range(21)]}
    )
    assert result["is_error"] is True
    assert "at most 20 fields" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_press_key_returns_key(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "press_key").handler({"key": "Enter"})
    assert result["content"][0]["text"] == "Pressed Enter"


@pytest.mark.asyncio
async def test_select_option_passes_target_and_values(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "select_option").handler(
        {"target": "#sel", "values": ["1", "2"]}
    )
    assert result["content"][0]["text"] == "Selected ['1', '2'] at #sel"


@pytest.mark.asyncio
async def test_navigate_back_returns_text(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "navigate_back").handler({})
    assert result["content"][0]["text"] == "Went back"


@pytest.mark.asyncio
async def test_console_messages_formats_entries(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "console_messages").handler({})
    text = result["content"][0]["text"]
    assert "2026-11-23T00:00:00+00:00" in text
    assert "[log]" in text
    assert "hello" in text


@pytest.mark.asyncio
async def test_close_tool_closes_all_tabs(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "close").handler({})
    assert service.close_calls == [None]
    assert result["content"][0]["text"] == "Closed chat-abc-1. Open tabs: none."


@pytest.mark.asyncio
async def test_screenshot_saves_file_and_returns_image(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler(
        {"filename": "shots/page.png"}
    )
    saved = tmp_path / "shots" / "page.png"
    assert saved.read_bytes() == b"\x89PNG\r\n\x1a\n"
    texts = [block["text"] for block in result["content"] if block["type"] == "text"]
    assert "Saved to shots/page.png" in texts[0]
    image = next(block for block in result["content"] if block["type"] == "image")
    assert image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_screenshot_without_filename_does_not_write(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler({})
    assert list(tmp_path.iterdir()) == []
    texts = [block["text"] for block in result["content"] if block["type"] == "text"]
    assert "Saved to" not in texts[0]
    assert any(block["type"] == "image" for block in result["content"])


@pytest.mark.asyncio
async def test_screenshot_rejects_workspace_escape(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler(
        {"filename": "../outside.png"}
    )
    assert result["is_error"] is True
    assert "error:" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_screenshot_rejects_absolute_path(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler(
        {"filename": str(tmp_path.parent / "x.png")}
    )
    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_screenshot_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler({"filename": "link/x.png"})
    assert result["is_error"] is True


@pytest.mark.asyncio
async def test_screenshot_requires_png_suffix(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "take_screenshot").handler({"filename": "shot.jpg"})
    assert result["is_error"] is True
    assert ".png suffix" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tabs_list_reports_current_tab(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "tabs").handler({"action": "list"})
    assert "chat-abc-1" in result["content"][0]["text"]
    assert "https://example.com/" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tabs_new_records_url(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler(
        {"action": "new", "url": "https://example.com"}
    )
    assert service.new_calls == ["https://example.com"]
    assert "Opened tab 2" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tabs_select_passes_index(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler({"action": "select", "index": 2})
    assert service.select_calls == [2]
    assert "Selected tab" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tabs_select_without_index_is_error(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler({"action": "select"})
    assert result["is_error"] is True
    assert service.select_calls == []


@pytest.mark.asyncio
async def test_tabs_select_with_non_numeric_index_is_error(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler({"action": "select", "index": "abc"})
    assert result["is_error"] is True
    assert service.select_calls == []
    assert service.close_calls == []


@pytest.mark.asyncio
async def test_tabs_close_passes_index(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler({"action": "close", "index": 1})
    assert service.close_calls == [1]
    assert result["content"][0]["text"] == "Closed chat-abc-1. Open tabs: none."


@pytest.mark.asyncio
async def test_tabs_close_without_index_is_error(tmp_path) -> None:
    service = FakeService()
    tools = build_tools(tmp_path, service=service)
    result = await find(tools, "tabs").handler({"action": "close"})
    assert result["is_error"] is True
    assert service.close_calls == []


@pytest.mark.asyncio
async def test_tabs_unknown_action_is_error(tmp_path) -> None:
    tools = build_tools(tmp_path)
    result = await find(tools, "tabs").handler({"action": "bogus"})
    assert result["is_error"] is True


def test_build_server_config(tmp_path) -> None:
    server = browser_mcp.build_server(
        FakeService(), agent_id="default", session_id="abc-1", workspace=tmp_path
    )
    assert server["type"] == "sdk"
    assert server["name"] == "browser"
    assert server["timeout"] == browser_mcp.TOOL_TIMEOUT_MS
    assert server["disableAutoBackground"] is True
