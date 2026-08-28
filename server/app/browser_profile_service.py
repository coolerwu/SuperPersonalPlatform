from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.infrastructure.browser_tools import BrowserToolError, _validate_public_url, browser_playwright_options, browser_profile_dir, prepare_browser_context


class BrowserAuthSessionNotFoundError(KeyError):
    pass


class BrowserProfileInUseError(RuntimeError):
    pass


class BrowserAuthUnavailableError(RuntimeError):
    pass


@dataclass
class BrowserProfileLock:
    agent_id: str
    profile_dir: Path
    lock_path: Path
    owner: str

    def release(self) -> None:
        try:
            current = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if current.get("owner") == self.owner:
            self.lock_path.unlink(missing_ok=True)


@dataclass
class BrowserAuthSession:
    id: str
    agent_id: str
    profile_dir: Path
    url: str
    created_at: float
    updated_at: float
    status: str = "running"
    error: str = ""
    playwright: Any = None
    context: Any = None
    page: Any = None
    lock: BrowserProfileLock | None = None
    mutex: asyncio.Lock = field(default_factory=asyncio.Lock)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "profile_path": self.profile_dir.as_posix(),
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "error": self.error,
        }


class BrowserProfileService:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.sessions: dict[str, BrowserAuthSession] = {}

    def profile_dir(self, agent_id: str) -> Path:
        return browser_profile_dir(self.workspace, agent_id)

    def profiles(self, agent_ids: list[str]) -> list[dict[str, Any]]:
        items = []
        for agent_id in agent_ids:
            profile_dir = self.profile_dir(agent_id)
            lock = _read_profile_lock(profile_dir)
            active_session = next((session.id for session in self.sessions.values() if session.agent_id == agent_id), "")
            items.append(
                {
                    "agent_id": agent_id,
                    "profile_path": profile_dir.as_posix(),
                    "exists": profile_dir.exists(),
                    "locked": bool(lock),
                    "lock": lock,
                    "active_session_id": active_session,
                    "modified_at": profile_dir.stat().st_mtime if profile_dir.exists() else None,
                }
            )
        return items

    async def start_session(
        self,
        *,
        agent_id: str,
        url: str = "",
        proxy: str = "",
        timeout_ms: int = 60000,
    ) -> dict[str, Any]:
        session_id = f"browser_auth_{uuid.uuid4().hex[:12]}"
        profile_dir = self.profile_dir(agent_id)
        lock = _acquire_profile_lock(profile_dir, agent_id=agent_id, owner=session_id, purpose="browser_auth")
        session = BrowserAuthSession(
            id=session_id,
            agent_id=_validate_agent_id(agent_id),
            profile_dir=profile_dir,
            url=url,
            created_at=time.time(),
            updated_at=time.time(),
            lock=lock,
        )
        self.sessions[session_id] = session
        try:
            await self._open_session(session, url=url, proxy=proxy, timeout_ms=timeout_ms)
        except Exception as exc:
            session.status = "failed"
            session.error = str(exc)
            await self._close_session(session)
            raise
        return session.summary()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return self._session(session_id).summary()

    async def screenshot(self, session_id: str) -> bytes:
        session = self._session(session_id)
        async with session.mutex:
            session.updated_at = time.time()
            return await session.page.screenshot(type="png", full_page=False)

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        session = self._session(session_id)
        async with session.mutex:
            await session.page.goto(url, wait_until="domcontentloaded")
            session.url = session.page.url
            session.updated_at = time.time()
        return session.summary()

    async def click(self, session_id: str, x: float, y: float) -> dict[str, Any]:
        session = self._session(session_id)
        async with session.mutex:
            await session.page.mouse.click(float(x), float(y))
            await session.page.wait_for_timeout(400)
            session.url = session.page.url
            session.updated_at = time.time()
        return session.summary()

    async def type_text(self, session_id: str, text: str) -> dict[str, Any]:
        session = self._session(session_id)
        async with session.mutex:
            await session.page.keyboard.type(str(text), delay=10)
            session.updated_at = time.time()
        return session.summary()

    async def press_key(self, session_id: str, key: str) -> dict[str, Any]:
        session = self._session(session_id)
        async with session.mutex:
            await session.page.keyboard.press(str(key or "Enter"))
            await session.page.wait_for_timeout(300)
            session.url = session.page.url
            session.updated_at = time.time()
        return session.summary()

    async def finish(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        session.status = "finished"
        session.updated_at = time.time()
        summary = session.summary()
        await self._close_session(session)
        return summary

    async def cancel(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        session.status = "cancelled"
        session.updated_at = time.time()
        summary = session.summary()
        await self._close_session(session)
        return summary

    async def close_all(self) -> None:
        for session in list(self.sessions.values()):
            await self._close_session(session)

    async def _open_session(
        self,
        session: BrowserAuthSession,
        *,
        url: str,
        proxy: str,
        timeout_ms: int,
    ) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserAuthUnavailableError("browser auth requires playwright") from exc

        launch_kwargs, context_kwargs = browser_playwright_options(proxy)

        timeout = max(1000, int(timeout_ms or 60000))
        session.playwright = await async_playwright().start()
        session.context = await session.playwright.chromium.launch_persistent_context(
            str(session.profile_dir),
            **launch_kwargs,
            **context_kwargs,
        )
        await prepare_browser_context(session.context)
        session.context.set_default_timeout(timeout)
        session.context.set_default_navigation_timeout(timeout)
        session.page = session.context.pages[0] if session.context.pages else await session.context.new_page()
        session.page.set_default_timeout(timeout)
        session.page.set_default_navigation_timeout(timeout)
        if url:
            _validate_public_url(url)
            await session.page.goto(url, wait_until="domcontentloaded")
            session.url = session.page.url

    async def _close_session(self, session: BrowserAuthSession) -> None:
        self.sessions.pop(session.id, None)
        try:
            if session.context is not None:
                await session.context.close()
        finally:
            try:
                if session.playwright is not None:
                    await session.playwright.stop()
            finally:
                if session.lock is not None:
                    session.lock.release()

    def _session(self, session_id: str) -> BrowserAuthSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise BrowserAuthSessionNotFoundError(session_id)
        return session


def _validate_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise BrowserToolError("invalid agent_id for browser profile")
    return value


def _acquire_profile_lock(profile_dir: Path, *, agent_id: str, owner: str, purpose: str) -> BrowserProfileLock:
    agent_id = _validate_agent_id(agent_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / "profile.lock.json"
    _clear_stale_lock(lock_path)
    payload = {
        "owner": owner,
        "agent_id": agent_id,
        "purpose": purpose,
        "pid": os.getpid(),
        "created_at": time.time(),
    }
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise BrowserProfileInUseError(f"browser profile for agent {agent_id} is already in use") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return BrowserProfileLock(agent_id=agent_id, profile_dir=profile_dir, lock_path=lock_path, owner=owner)


def _read_profile_lock(profile_dir: Path) -> dict[str, Any] | None:
    lock_path = profile_dir / "profile.lock.json"
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {"owner": "unknown", "purpose": "unknown", "created_at": lock_path.stat().st_mtime}


def _clear_stale_lock(lock_path: Path, *, stale_seconds: int = 3600) -> None:
    if not lock_path.exists():
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if _lock_owner_process_is_dead(payload):
        lock_path.unlink(missing_ok=True)
        return
    age = time.time() - lock_path.stat().st_mtime
    if "pid" not in payload and age > stale_seconds:
        lock_path.unlink(missing_ok=True)


def _lock_owner_process_is_dead(payload: Any) -> bool:
    if not isinstance(payload, dict) or "pid" not in payload:
        return False
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return True
    if pid < 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False
