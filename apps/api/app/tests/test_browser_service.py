from __future__ import annotations

import asyncio

import pytest

from app.services.browser_service import BrowserService
from app.services.browser_tools import BrowserToolError


class FakeSession:
    def __init__(self, tabs: list[dict] | None = None) -> None:
        self.closed = False
        self.tabs = [dict(tab) for tab in (tabs or [])]
        self.closed_tabs: list[str] = []
        self.opened: list[tuple[str, str]] = []
        self.aclose_calls = 0

    async def tabs_state(self) -> list[dict]:
        await asyncio.sleep(0)
        return [dict(tab) for tab in self.tabs]

    async def new_tab(self, name: str) -> str:
        await asyncio.sleep(0)
        self.tabs.append({"name": name, "url": "about:blank"})
        return name

    async def open(self, url: str, name: str) -> str:
        self.opened.append((url, name))
        for tab in self.tabs:
            if tab["name"] == name:
                tab["url"] = url
        return f"Opened {url}"

    async def close_tab(self, name: str) -> str:
        if not any(tab["name"] == name for tab in self.tabs):
            raise BrowserToolError(f"tab {name!r} is not open")
        self.closed_tabs.append(name)
        self.tabs = [tab for tab in self.tabs if tab["name"] != name]
        return f"Closed {name}"

    async def aclose(self) -> None:
        self.aclose_calls += 1
        self.closed = True


def make_service(session: FakeSession, **kwargs) -> BrowserService:
    return BrowserService(session_factory=lambda: session, **kwargs)


class FailingOpenSession(FakeSession):
    async def open(self, url: str, name: str) -> str:
        raise BrowserToolError("navigation failed")


@pytest.mark.asyncio
async def test_prefix_sanitizes_session_id() -> None:
    assert BrowserService.prefix("0e1bd714-2246-41b3-91a1-5464e63174d3") == "chat-0e1bd714"
    assert BrowserService.prefix("!!!") == "chat-unnamed"


@pytest.mark.asyncio
async def test_resolve_tab_defaults_and_index() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        first = await service.resolve_tab(live, "abc-1")
        assert first == "chat-abc-1"
        await session.new_tab("chat-abc-1")
        await session.new_tab("chat-abc-1-2")
        assert await service.resolve_tab(live, "abc-1", "2") == "chat-abc-1-2"
        assert await service.resolve_tab(live, "abc-1", "chat-abc-1") == "chat-abc-1"
        with pytest.raises(BrowserToolError):
            await service.resolve_tab(live, "abc-1", "9")
        with pytest.raises(BrowserToolError):
            await service.resolve_tab(live, "abc-1", "chat-other-1")


@pytest.mark.asyncio
async def test_new_tab_names_are_sequential_and_open_url() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        info = await service.new_tab(live, "abc-1", url="https://example.com")
        assert info == {"index": 1, "name": "chat-abc-1"}
        second = await service.new_tab(live, "abc-1")
        assert second == {"index": 2, "name": "chat-abc-1-2"}

    assert session.opened == [("https://example.com", "chat-abc-1")]


@pytest.mark.asyncio
async def test_concurrent_new_tabs_do_not_collide() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        first, second = await asyncio.gather(
            service.new_tab(live, "abc-1"),
            service.new_tab(live, "abc-1"),
        )

    assert {first["name"], second["name"]} == {"chat-abc-1", "chat-abc-1-2"}


@pytest.mark.asyncio
async def test_close_tabs_only_touches_own_session() -> None:
    session = FakeSession(
        [
            {"name": "chat-abc-1", "url": "about:blank"},
            {"name": "chat-abc-1-2", "url": "about:blank"},
            {"name": "chat-def-1", "url": "about:blank"},
        ]
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        message = await service.close_tabs(live, "abc-1")

    assert session.closed_tabs == ["chat-abc-1", "chat-abc-1-2"]
    assert [tab["name"] for tab in session.tabs] == ["chat-def-1"]
    assert "chat-def-1" not in message


@pytest.mark.asyncio
async def test_select_tab_changes_default() -> None:
    session = FakeSession(
        [
            {"name": "chat-abc-1", "url": "about:blank"},
            {"name": "chat-abc-1-2", "url": "about:blank"},
        ]
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        await service.select_tab(live, "abc-1", "2")
        assert await service.resolve_tab(live, "abc-1") == "chat-abc-1-2"


@pytest.mark.asyncio
async def test_service_recreates_closed_session() -> None:
    first = FakeSession()
    sessions = [first, FakeSession()]
    service = BrowserService(
        session_factory=lambda: sessions.pop(0), idle_seconds=300
    )

    async with service.use() as live:
        assert live is first
    first.closed = True
    async with service.use() as live:
        assert live is not first

    await service.shutdown()


@pytest.mark.asyncio
async def test_idle_reaper_closes_session() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=0.02, sweep_seconds=0.01)
    await service.startup()

    async with service.use():
        pass
    await asyncio.sleep(0.12)

    assert session.aclose_calls == 1
    assert service._session is None  # noqa: SLF001 - asserts internal cleanup
    await service.shutdown()


@pytest.mark.asyncio
async def test_shutdown_closes_browser_and_cancels_reaper() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)
    await service.startup()

    async with service.use():
        pass
    await service.shutdown()

    assert session.aclose_calls == 1
    assert service._reaper is None  # noqa: SLF001
    with pytest.raises(BrowserToolError):
        async with service.use():
            pass


@pytest.mark.asyncio
async def test_new_tab_index_matches_list_position_after_close() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        assert (await service.new_tab(live, "abc-1"))["index"] == 1
        assert (await service.new_tab(live, "abc-1"))["index"] == 2
        await service.close_tabs(live, "abc-1", "1")
        third = await service.new_tab(live, "abc-1")
        rows = await service.list_tabs(live, "abc-1")
        position = next(row["index"] for row in rows if row["name"] == third["name"])
        assert third["index"] == position
        assert await service.resolve_tab(live, "abc-1", str(third["index"])) == third["name"]


@pytest.mark.asyncio
async def test_resolve_tab_rejects_zero_index() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        await session.new_tab("chat-abc-1")
        with pytest.raises(BrowserToolError):
            await service.resolve_tab(live, "abc-1", 0)


@pytest.mark.asyncio
async def test_resolve_tab_allows_explicit_base_name_with_no_tabs() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        assert await service.resolve_tab(live, "abc-1", "chat-abc-1") == "chat-abc-1"
        with pytest.raises(BrowserToolError):
            await service.resolve_tab(live, "abc-1", "chat-other-1")
        with pytest.raises(BrowserToolError):
            await service.resolve_tab(live, "abc-1", 1)


@pytest.mark.asyncio
async def test_list_tabs_reports_indices_and_default() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        await service.new_tab(live, "abc-1", url="https://a.example")
        await service.new_tab(live, "abc-1")
        await service.select_tab(live, "abc-1", "2")
        rows = await service.list_tabs(live, "abc-1")

    assert [row["index"] for row in rows] == [1, 2]
    assert [row["name"] for row in rows] == ["chat-abc-1", "chat-abc-1-2"]
    assert [row["default"] for row in rows] == [False, True]
    assert rows[0]["url"] == "https://a.example"


@pytest.mark.asyncio
async def test_reset_closes_session_and_next_use_is_fresh() -> None:
    first = FakeSession()
    second = FakeSession()
    sessions = [first, second]
    service = BrowserService(
        session_factory=lambda: sessions.pop(0), idle_seconds=300
    )

    async with service.use() as live:
        assert live is first
    await service.reset()
    assert first.aclose_calls == 1
    async with service.use() as live:
        assert live is second

    await service.shutdown()


@pytest.mark.asyncio
async def test_reset_skips_newer_session_after_earlier_reset() -> None:
    first = FakeSession()
    second = FakeSession()
    third = FakeSession()
    sessions = [first, second, third]
    service = BrowserService(
        session_factory=lambda: sessions.pop(0), idle_seconds=300
    )

    async with service.use() as live:
        assert live is first
    await service.reset()
    assert first.aclose_calls == 1

    async with service.use() as live:
        assert live is second
    # 新会话在最近一次 reset 之后创建，级联 reset 不应连带关闭它。
    await service.reset()
    assert second.aclose_calls == 0

    # 模拟旧会话崩溃：use() 创建新会话，reset 仍保护这个更新会话。
    second.closed = True
    async with service.use() as live:
        assert live is third
    await service.reset()
    assert third.aclose_calls == 0

    await service.shutdown()


@pytest.mark.asyncio
async def test_idle_reaper_skips_active_session() -> None:
    session = FakeSession()
    service = make_service(session, idle_seconds=0.02, sweep_seconds=0.01)
    await service.startup()

    async with service.use():
        await asyncio.sleep(0.12)
        assert session.aclose_calls == 0
        assert service._session is session  # noqa: SLF001
    await asyncio.sleep(0.12)

    assert session.aclose_calls == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_close_tabs_clears_default() -> None:
    session = FakeSession(
        [
            {"name": "chat-abc-1", "url": "about:blank"},
            {"name": "chat-abc-1-2", "url": "about:blank"},
        ]
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        await service.select_tab(live, "abc-1", "2")
        await service.close_tabs(live, "abc-1")

    assert service._default_tabs.get("abc-1") is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_stale_default_falls_back() -> None:
    session = FakeSession(
        [
            {"name": "chat-abc-1", "url": "about:blank"},
            {"name": "chat-abc-1-2", "url": "about:blank"},
        ]
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        service._default_tabs["abc-1"] = "chat-abc-1-9"  # noqa: SLF001
        assert await service.resolve_tab(live, "abc-1") == "chat-abc-1"


@pytest.mark.asyncio
async def test_new_tab_index_is_stable_against_concurrent_close() -> None:
    session = FakeSession(
        [
            {"name": "chat-abc-1", "url": "about:blank"},
            {"name": "chat-abc-1-2", "url": "about:blank"},
        ]
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        _, created = await asyncio.gather(
            service.close_tabs(live, "abc-1", "1"),
            service.new_tab(live, "abc-1"),
        )
        rows = await service.list_tabs(live, "abc-1")

    position = next(row["index"] for row in rows if row["name"] == created["name"])
    assert created["index"] == position


@pytest.mark.asyncio
async def test_new_tab_closes_orphan_when_open_fails() -> None:
    session = FailingOpenSession()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        with pytest.raises(BrowserToolError):
            await service.new_tab(live, "abc-1", url="https://example.com")

    assert session.closed_tabs == ["chat-abc-1"]
    assert session.tabs == []
