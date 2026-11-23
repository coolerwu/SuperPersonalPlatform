from __future__ import annotations

import asyncio

import pytest

from app.services.browser_service import BrowserService
from app.services.browser_tools import BrowserToolError


class FakeSession:
    def __init__(self, tabs: list[dict] | None = None, *, concurrency: int = 2) -> None:
        self.closed = False
        self.concurrency = concurrency
        self.tabs = [dict(tab) for tab in (tabs or [])]
        self.closed_tabs: list[str] = []
        self.dropped_tabs: list[str] = []
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

    async def drop_tab(self, name: str) -> str:
        if not any(tab["name"] == name for tab in self.tabs):
            raise BrowserToolError(f"tab {name!r} is not open")
        self.dropped_tabs.append(name)
        self.tabs = [tab for tab in self.tabs if tab["name"] != name]
        return f"Dropped {name}"

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
async def test_reset_debounce_skips_newer_session_within_window() -> None:
    first = FakeSession()
    second = FakeSession()
    third = FakeSession()
    sessions = [first, second, third]
    service = BrowserService(
        session_factory=lambda: sessions.pop(0),
        idle_seconds=300,
        reset_debounce_seconds=60,
    )

    async with service.use() as live:
        assert live is first
    await service.reset()
    assert first.aclose_calls == 1

    async with service.use() as live:
        assert live is second
    # 新会话在防抖窗口内，级联 reset 不应连带关闭它。
    await service.reset()
    assert second.aclose_calls == 0

    # 旧会话崩溃后 use() 重建的新会话同样受窗口保护。
    second.closed = True
    async with service.use() as live:
        assert live is third
    await service.reset()
    assert third.aclose_calls == 0

    await service.shutdown()


@pytest.mark.asyncio
async def test_reset_recovers_after_debounce_window() -> None:
    first = FakeSession()
    second = FakeSession()
    third = FakeSession()
    sessions = [first, second, third]
    service = BrowserService(
        session_factory=lambda: sessions.pop(0),
        idle_seconds=300,
        reset_debounce_seconds=0.01,
    )

    async with service.use() as live:
        assert live is first
    await service.reset()
    assert first.aclose_calls == 1

    async with service.use() as live:
        assert live is second
    await asyncio.sleep(0.05)
    await service.reset()
    # 防抖窗口过后 reset 仍能恢复，第二次崩溃会真正关闭当前会话。
    assert first.aclose_calls + second.aclose_calls == 2
    assert second.aclose_calls == 1

    async with service.use() as live:
        assert live is third

    await service.shutdown()


@pytest.mark.asyncio
async def test_concurrent_resets_within_window_recreate_once() -> None:
    created: list[FakeSession] = []

    def factory() -> FakeSession:
        session = FakeSession()
        created.append(session)
        return session

    service = BrowserService(
        session_factory=factory,
        idle_seconds=300,
        reset_debounce_seconds=60,
    )

    async with service.use() as live:
        assert live is created[0]
    await service.reset()
    assert created[0].aclose_calls == 1

    async with service.use() as live:
        assert live is created[1]
    # 新会话正处于防抖窗口内，多个并发 reset 都属于同一次事故：
    # 既不关闭刚重建的新会话，也不额外创建新会话。
    await asyncio.gather(service.reset(), service.reset(), service.reset())
    assert created[1].aclose_calls == 0
    assert len(created) == 2


@pytest.mark.asyncio
async def test_prepare_tab_evicts_oldest_other_session_tab() -> None:
    session = FakeSession(
        [
            {"name": "chat-aaa-1", "url": "about:blank"},
            {"name": "chat-bbb-1", "url": "about:blank"},
            {"name": "chat-bbb-1-2", "url": "about:blank"},
            {"name": "chat-ccc-1", "url": "about:blank"},
            {"name": "chat-ccc-1-2", "url": "about:blank"},
        ],
        concurrency=5,
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        name = await service.prepare_tab(live, "ddd-1")

    assert name == "chat-ddd-1"
    # 只驱逐最老的其它会话标签页，其余会话标签保持不变。
    assert session.dropped_tabs == ["chat-aaa-1"]
    assert session.closed_tabs == []
    remaining = {tab["name"] for tab in session.tabs}
    assert "chat-aaa-1" not in remaining
    assert remaining == {
        "chat-bbb-1",
        "chat-bbb-1-2",
        "chat-ccc-1",
        "chat-ccc-1-2",
    }


@pytest.mark.asyncio
async def test_new_tab_raises_when_all_tabs_belong_to_caller() -> None:
    session = FakeSession(
        [
            {"name": "chat-aaa-1", "url": "about:blank"},
            {"name": "chat-aaa-1-2", "url": "about:blank"},
            {"name": "chat-aaa-1-3", "url": "about:blank"},
            {"name": "chat-aaa-1-4", "url": "about:blank"},
            {"name": "chat-aaa-1-5", "url": "about:blank"},
        ],
        concurrency=5,
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        # 通过公开的 new_tab 触发配额错误；本会话自己的标签页不能被驱逐。
        with pytest.raises(BrowserToolError, match="using all 5 shared browser tabs"):
            await service.new_tab(live, "aaa-1")

    assert session.dropped_tabs == []
    assert session.closed_tabs == []


@pytest.mark.asyncio
async def test_new_tab_at_capacity_evicts_other_session_first() -> None:
    session = FakeSession(
        [
            {"name": "chat-aaa-1", "url": "about:blank"},
            {"name": "chat-aaa-1-2", "url": "about:blank"},
            {"name": "chat-aaa-1-3", "url": "about:blank"},
        ],
        concurrency=3,
    )
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        info = await service.new_tab(live, "bbb-1")

    assert info["name"] == "chat-bbb-1"
    assert session.dropped_tabs == ["chat-aaa-1"]
    assert "chat-aaa-1" not in {tab["name"] for tab in session.tabs}
    assert "chat-bbb-1" in {tab["name"] for tab in session.tabs}

    await service.shutdown()


@pytest.mark.asyncio
async def test_eviction_uses_drop_tab_without_waiting_on_operation_lock() -> None:
    class LockedSession(FakeSession):
        def __init__(self, tabs: list[dict], *, concurrency: int) -> None:
            super().__init__(tabs, concurrency=concurrency)
            self.operation_lock = asyncio.Lock()

        async def close_tab(self, name: str) -> str:
            # 正常关闭要等操作锁；驱逐必须走 drop_tab 才不会被挂起的动作阻塞。
            async with self.operation_lock:
                return await super().close_tab(name)

    session = LockedSession(
        [
            {"name": "chat-aaa-1", "url": "about:blank"},
            {"name": "chat-bbb-1", "url": "about:blank"},
        ],
        concurrency=2,
    )
    await session.operation_lock.acquire()
    service = make_service(session, idle_seconds=300)

    async with service.use() as live:
        name = await asyncio.wait_for(
            service.prepare_tab(live, "ccc-1"), timeout=0.5
        )

    assert name == "chat-ccc-1"
    assert session.dropped_tabs == ["chat-aaa-1"]


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
