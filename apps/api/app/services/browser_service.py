"""Process-wide browser shared by every session.

One Chromium browser + context lives in the API process. Cookie/localStorage
state is persisted to ``browser_state.json`` by ``BrowserSession`` so logins
survive restarts. Each session owns a tab namespace (``chat-<id>-N``), and the
service reaps the whole browser after an idle period.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator, Callable

from app.config import settings
from app.services.browser_tools import BrowserSession, BrowserToolError

logger = logging.getLogger(__name__)

TAB_PREFIX = "chat-"


class BrowserService:
    """Owns the shared browser and hands out per-session tab names."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], BrowserSession] | None = None,
        idle_seconds: float | None = None,
        sweep_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory or BrowserSession
        self._idle_seconds = max(
            0.01,
            float(
                idle_seconds
                if idle_seconds is not None
                else settings.browser_idle_seconds
            ),
        )
        self._sweep_seconds = max(0.01, float(sweep_seconds))
        self._session: BrowserSession | None = None
        self._lock = asyncio.Lock()
        self._active = 0
        self._last_used = time.monotonic()
        self._default_tabs: dict[str, str] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._closed = False

    async def startup(self) -> None:
        if self._closed or self._reaper is not None:
            return
        self._reaper = asyncio.create_task(
            self._reap(), name="browser-idle-reaper"
        )

    async def shutdown(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            reaper = self._reaper
            self._reaper = None
            session = self._session
            self._session = None
            self._default_tabs.clear()
        if reaper is not None:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
        if session is None:
            return
        try:
            await asyncio.wait_for(session.aclose(), timeout=15)
        except Exception:
            logger.warning("browser close timed out during shutdown", exc_info=True)

    @asynccontextmanager
    async def use(self) -> AsyncIterator[BrowserSession]:
        if self._closed:
            raise BrowserToolError("browser service is shutting down")
        session = await self._ensure_session()
        self._active += 1
        try:
            yield session
        finally:
            self._active -= 1
            self._last_used = time.monotonic()

    async def _ensure_session(self) -> BrowserSession:
        async with self._lock:
            if self._closed:
                raise BrowserToolError("browser service is shutting down")
            if self._session is None or self._session.closed:
                self._session = self._session_factory()
                logger.info("browser started for shared sessions")
            self._last_used = time.monotonic()
            return self._session

    async def reset(self) -> None:
        async with self._lock:
            session = self._session
            self._session = None
            self._default_tabs.clear()
        if session is None:
            return
        try:
            await asyncio.wait_for(session.aclose(), timeout=10)
        except Exception:
            logger.debug("browser close failed during reset", exc_info=True)
        else:
            logger.info("browser reset; the next request starts a fresh one")

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_seconds)
            if self._closed or self._active > 0:
                continue
            if time.monotonic() - self._last_used < self._idle_seconds:
                continue
            async with self._lock:
                # 活跃计数与空闲判断都要在锁内复核，避免刚被取用就被回收。
                if self._closed or self._active > 0:
                    continue
                idle = time.monotonic() - self._last_used
                if idle < self._idle_seconds:
                    continue
                session = self._session
                self._session = None
                self._default_tabs.clear()
            if session is None:
                continue
            try:
                await asyncio.wait_for(session.aclose(), timeout=12)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("browser close failed after idle timeout", exc_info=True)
            else:
                logger.info("browser closed after %.0f s idle", idle)

    @staticmethod
    def prefix(session_id: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_-]", "", str(session_id or ""))[:8]
        return f"{TAB_PREFIX}{clean or 'unnamed'}"

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def _names(self, session: BrowserSession, session_id: str) -> list[str]:
        base = self.prefix(session_id)
        nested = f"{base}-"
        names: list[str] = []
        for tab in await session.tabs_state():
            name = str(tab.get("name") or "")
            if name == base or name.startswith(nested):
                names.append(name)
        return names

    async def resolve_tab(
        self, session: BrowserSession, session_id: str, tab: str | int | None = None
    ) -> str:
        base = self.prefix(session_id)
        # tab=0 是显式下标，不能被当作“未指定”。
        requested = "" if tab is None else str(tab).strip()
        names = await self._names(session, session_id)
        if not names:
            if requested and requested != base:
                raise BrowserToolError(
                    f"tab {requested!r} does not belong to this session; "
                    "call tabs(action='list') first"
                )
            return base
        if not requested:
            stored = self._default_tabs.get(session_id)
            return stored if stored in names else names[0]
        if requested.isascii() and requested.isdecimal():
            index = int(requested)
            if 1 <= index <= len(names):
                return names[index - 1]
            raise BrowserToolError(
                f"tab index {index} is out of range; this session has "
                f"{len(names)} tab(s)"
            )
        if requested in names:
            return requested
        raise BrowserToolError(
            f"tab {requested!r} does not belong to this session; "
            "call tabs(action='list') first"
        )

    async def list_tabs(
        self, session: BrowserSession, session_id: str
    ) -> list[dict[str, object]]:
        base = self.prefix(session_id)
        nested = f"{base}-"
        rows: list[dict[str, str]] = []
        for tab in await session.tabs_state():
            name = str(tab.get("name") or "")
            if name == base or name.startswith(nested):
                rows.append({"name": name, "url": str(tab.get("url") or "")})
        default = self._default_tabs.get(session_id)
        if default not in [row["name"] for row in rows]:
            default = rows[0]["name"] if rows else None
        return [
            {
                "index": index,
                "name": row["name"],
                "url": row["url"],
                "default": row["name"] == default,
            }
            for index, row in enumerate(rows, 1)
        ]

    async def new_tab(
        self,
        session: BrowserSession,
        session_id: str,
        url: str | None = None,
    ) -> dict[str, object]:
        base = self.prefix(session_id)
        async with self._session_lock(session_id):
            # 分配与建页必须在同一把会话锁内，否则并发 new 会抢到同一个名字。
            names = await self._names(session, session_id)
            taken = set(names)
            if base not in taken:
                name = base
            else:
                suffix = 2
                while f"{base}-{suffix}" in taken:
                    suffix += 1
                name = f"{base}-{suffix}"
            await session.new_tab(name)
        # 导航可能长达 30 秒，放到锁外，避免阻塞同会话的其他标签操作。
        if url:
            await session.open(str(url), name)
        return {"index": len(names) + 1, "name": name}

    async def select_tab(
        self,
        session: BrowserSession,
        session_id: str,
        tab: str | int | None = None,
    ) -> str:
        name = await self.resolve_tab(session, session_id, tab)
        self._default_tabs[session_id] = name
        return name

    async def close_tabs(
        self,
        session: BrowserSession,
        session_id: str,
        tab: str | int | None = None,
    ) -> str:
        # tab=0 是显式下标，不能被当作“关闭全部”。
        requested = "" if tab is None else str(tab).strip()
        names = await self._names(session, session_id)
        if not names:
            return "No tabs are open for this session."
        if requested:
            targets = [await self.resolve_tab(session, session_id, requested)]
        else:
            targets = names
        closed: list[str] = []
        for name in targets:
            try:
                await session.close_tab(name)
            except BrowserToolError:
                continue
            closed.append(name)
            if self._default_tabs.get(session_id) == name:
                self._default_tabs.pop(session_id, None)
        remaining = await self._names(session, session_id)
        if self._default_tabs.get(session_id) not in remaining:
            self._default_tabs.pop(session_id, None)
        return (
            f"Closed {len(closed)} tab(s). "
            f"Open tabs: {', '.join(remaining) or 'none'}."
        )
