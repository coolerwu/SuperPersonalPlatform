from __future__ import annotations

import asyncio

import pytest

from app.services.browser_tools import BrowserSession, BrowserToolError, _Tab


class FakeLocator:
    def __init__(self, selector: str, count: int = 1) -> None:
        self.selector = selector
        self._count = count
        self.first = self
        self.filled: list[str] = []
        self.selected: list[dict] = []
        self.clicked = 0

    async def count(self) -> int:
        return self._count

    async def evaluate(self, _script: str) -> str:
        return "input"

    async def fill(self, text: str, timeout: int) -> None:
        self.filled.append(text)

    async def select_option(self, **kwargs) -> None:
        self.selected.append(kwargs)

    async def click(self, timeout: int) -> None:
        self.clicked += 1


class FallbackLocator(FakeLocator):
    async def select_option(self, **kwargs) -> None:
        self.selected.append(kwargs)
        if "value" in kwargs:
            raise RuntimeError("no matching value")


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    def __init__(self) -> None:
        self.locators: dict[str, FakeLocator] = {}
        self.keyboard = FakeKeyboard()
        self.url = "https://example.com/"
        self.back_result: object | None = object()
        self.waited: list[int] = []
        self.text_waits: list[tuple[str, str]] = []
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.setdefault(selector, FakeLocator(selector))

    def get_by_text(self, text: str, exact: bool = False):
        page = self

        class _Text:
            def __init__(self) -> None:
                self.first = self

            async def wait_for(self, state: str, timeout: int) -> None:
                page.text_waits.append((text, state))

        return _Text()

    async def wait_for_timeout(self, ms: int) -> None:
        self.waited.append(ms)

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        return None

    async def go_back(self, timeout: int):
        return self.back_result

    async def title(self) -> str:
        return "Example"


def make_session(page: FakePage, *, concurrency: int = 2) -> BrowserSession:
    session = BrowserSession(concurrency=concurrency)
    session._tabs["main"] = _Tab(name="main", page=page, lock=asyncio.Lock())

    async def _snapshot(tab_state):  # noqa: ANN001
        return f"SNAP:{tab_state.name}"

    async def _settle(_page):  # noqa: ANN001
        return None

    session._take_snapshot = _snapshot  # type: ignore[method-assign]
    session._wait_after_action = _settle  # type: ignore[method-assign]
    return session


@pytest.mark.asyncio
async def test_click_accepts_css_selector() -> None:
    page = FakePage()
    session = make_session(page)

    output = await session.click("#go", "main")

    assert page.locator("#go").clicked == 1
    assert "SNAP:main" in output


@pytest.mark.asyncio
async def test_click_keeps_ref_support() -> None:
    page = FakePage()
    session = make_session(page)

    await session.click("s1e3", "main")

    assert page.locator('[data-sp-ref="s1e3"]').clicked == 1


@pytest.mark.asyncio
async def test_select_option_falls_back_to_label() -> None:
    page = FakePage()
    page.locators["#sel"] = FallbackLocator("#sel")
    session = make_session(page)

    output = await session.select_option("#sel", ["高速"], "main")

    calls = page.locators["#sel"].selected
    assert calls[0] == {
        "value": ["高速"],
        "timeout": session.action_timeout_ms,
    }
    assert calls[1]["label"] == ["高速"]
    assert "SNAP:main" in output


@pytest.mark.asyncio
async def test_wait_for_requires_a_condition() -> None:
    session = make_session(FakePage())

    with pytest.raises(BrowserToolError):
        await session.wait_for("main")


@pytest.mark.asyncio
async def test_wait_for_text_and_time() -> None:
    page = FakePage()
    session = make_session(page)

    output = await session.wait_for("main", text="加载完成", time_ms=1_000)

    assert ("加载完成", "visible") in page.text_waits
    assert page.waited == [1_000]
    assert "SNAP:main" in output


@pytest.mark.asyncio
async def test_navigate_back_errors_without_history() -> None:
    page = FakePage()
    page.back_result = None
    session = make_session(page)

    with pytest.raises(BrowserToolError):
        await session.navigate_back("main")


@pytest.mark.asyncio
async def test_fill_many_fills_each_field() -> None:
    page = FakePage()
    session = make_session(page)

    output = await session.fill_many(
        [{"target": "#user", "value": "alice"}, {"target": "#pwd", "value": "secret"}],
        "main",
    )

    assert page.locator("#user").filled == ["alice"]
    assert page.locator("#pwd").filled == ["secret"]
    assert "SNAP:main" in output


@pytest.mark.asyncio
async def test_console_messages_reads_tab_buffer() -> None:
    session = make_session(FakePage())
    session._tabs["main"].console.append({"type": "error", "text": "boom"})

    entries = await session.console_messages("main")

    assert entries == [{"type": "error", "text": "boom"}]


@pytest.mark.asyncio
async def test_tabs_state_lists_tabs_with_urls() -> None:
    session = make_session(FakePage())

    state = await session.tabs_state()

    assert state == [{"name": "main", "url": "https://example.com/"}]


@pytest.mark.asyncio
async def test_click_rejects_ambiguous_selector() -> None:
    page = FakePage()
    page.locators[".item"] = FakeLocator(".item", count=2)
    session = make_session(page)

    with pytest.raises(BrowserToolError):
        await session.click(".item", "main")


class _FakeContext:
    def __init__(self, page) -> None:  # noqa: ANN001
        self.page = page

    async def new_page(self):  # noqa: ANN201
        return self.page


@pytest.mark.asyncio
async def test_console_listener_records_and_truncates() -> None:
    page = FakePage()
    session = BrowserSession(concurrency=3)

    async def _noop():
        return None

    session._ensure_started = _noop  # type: ignore[method-assign]
    session._context = _FakeContext(page)  # type: ignore[assignment]

    await session.new_tab("logs")
    handler = page.handlers["console"]

    class Message:
        type = "error"
        text = "x" * 600

    handler(Message())

    entries = await session.console_messages("logs")
    assert entries[0]["type"] == "error"
    assert entries[0]["text"] == "x" * 500
    assert entries[0]["time"]

