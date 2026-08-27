from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class BrowserToolError(ValueError):
    pass


class BrowserProfileInUseError(RuntimeError):
    pass


_DESKTOP_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class BrowserProfileLock:
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


def build_browser_extract_tool(
    *,
    proxy: str = "",
    timeout_ms: int = 60000,
    workspace: Path | None = None,
    agent_id: str = "",
) -> Any:
    from langchain_core.tools import StructuredTool

    async def browser_extract(url: str, include_links: bool = True, max_chars: int = 12000) -> str:
        """Open a public web page in a headless browser and extract rendered text."""
        _validate_public_url(url)
        max_chars = max(1000, min(int(max_chars or 12000), 50000))
        navigation_timeout_ms = max(1000, int(timeout_ms or 60000))
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("browser_extract requires playwright") from exc

        launch_kwargs, context_kwargs = browser_playwright_options(proxy)
        playwright = await async_playwright().start()
        profile_lock: BrowserProfileLock | None = None
        browser = None
        browser_context = None
        try:
            if workspace is not None and agent_id:
                profile_dir = browser_profile_dir(workspace, agent_id)
                profile_lock = acquire_browser_profile_lock(
                    profile_dir,
                    owner=f"browser_extract_{uuid.uuid4().hex}",
                    agent_id=agent_id,
                    purpose="browser_extract",
                )
                browser_context = await playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    **launch_kwargs,
                    **context_kwargs,
                )
            else:
                browser = await playwright.chromium.launch(**launch_kwargs)
                browser_context = await browser.new_context(**context_kwargs)
            await prepare_browser_context(browser_context)
            browser_context.set_default_timeout(navigation_timeout_ms)
            browser_context.set_default_navigation_timeout(navigation_timeout_ms)
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
            page.set_default_timeout(navigation_timeout_ms)
            page.set_default_navigation_timeout(navigation_timeout_ms)
            response = await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=min(navigation_timeout_ms, 5000))
            except Exception:
                pass
            status = f"HTTP {response.status}" if response is not None else "navigated"
            text = await page.locator("body").inner_text(timeout=navigation_timeout_ms)
            links = []
            if include_links:
                raw_links = await page.locator("a[href]").evaluate_all(
                    """elements => elements.map((element) => element.href).filter(Boolean)"""
                )
                if isinstance(raw_links, list):
                    links = [str(link) for link in raw_links if _is_http_url(str(link))][:100]

            payload = {
                "url": url,
                "status": status,
                "text": str(text)[:max_chars],
                "truncated": len(str(text)) > max_chars,
                "links": links,
            }
            if workspace is not None and agent_id:
                payload["profile_path"] = browser_profile_dir(workspace, agent_id).as_posix()
            return json.dumps(payload, ensure_ascii=False)
        finally:
            if browser_context is not None:
                await browser_context.close()
            if browser is not None:
                await browser.close()
            if profile_lock is not None:
                profile_lock.release()
            await playwright.stop()

    return StructuredTool.from_function(
        coroutine=browser_extract,
        name="browser_extract",
        description=(
            "Open a public http/https web page in a headless Playwright browser and extract rendered text and links. "
            "Use this for JavaScript-rendered pages when normal context search is insufficient. "
            "For Agent runs, the browser reuses that Agent's persistent profile under workspace/browser_profiles/{agent_id}. "
            "Args: url, include_links=true, max_chars. Private, localhost, and internal network URLs are blocked."
        ),
    )


def browser_profile_dir(workspace: Path, agent_id: str) -> Path:
    safe_agent_id = validate_browser_agent_id(agent_id)
    return workspace.resolve() / "browser_profiles" / safe_agent_id


def browser_playwright_options(proxy: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-default-browser-check",
            "--no-first-run",
        ],
    }
    context_kwargs: dict[str, Any] = {
        "viewport": {"width": 1365, "height": 900},
        "user_agent": _DESKTOP_CHROME_USER_AGENT,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    }
    proxy_url = _resolve_proxy(proxy)
    if proxy_url:
        launch_kwargs["proxy"] = {"server": proxy_url}
    return launch_kwargs, context_kwargs


async def prepare_browser_context(context: Any) -> None:
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        """
    )


def validate_browser_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise BrowserToolError("invalid agent_id for browser profile")
    return value


def acquire_browser_profile_lock(
    profile_dir: Path,
    *,
    owner: str,
    agent_id: str,
    purpose: str,
) -> BrowserProfileLock:
    safe_agent_id = validate_browser_agent_id(agent_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / "profile.lock.json"
    _clear_stale_profile_lock(lock_path)
    payload = {
        "owner": owner,
        "agent_id": safe_agent_id,
        "purpose": purpose,
        "created_at": time.time(),
    }
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise BrowserProfileInUseError(f"browser profile for agent {safe_agent_id} is already in use") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return BrowserProfileLock(profile_dir=profile_dir, lock_path=lock_path, owner=owner)


def read_browser_profile_lock(profile_dir: Path) -> dict[str, Any] | None:
    lock_path = profile_dir / "profile.lock.json"
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {"owner": "unknown", "purpose": "unknown", "created_at": lock_path.stat().st_mtime}


def _clear_stale_profile_lock(lock_path: Path, *, stale_seconds: int = 3600) -> None:
    if not lock_path.exists():
        return
    if time.time() - lock_path.stat().st_mtime > stale_seconds:
        lock_path.unlink(missing_ok=True)


def _resolve_proxy(configured_proxy: str) -> str:
    proxy = str(configured_proxy or "").strip()
    if proxy:
        return proxy
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BrowserToolError("browser_extract only supports absolute http/https URLs")
    if _is_blocked_host(parsed.hostname):
        raise BrowserToolError("browser_extract cannot access localhost, private, or internal network addresses")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise BrowserToolError(f"browser_extract cannot resolve host: {parsed.hostname}") from exc
    for address in addresses:
        if _is_blocked_ip(address):
            raise BrowserToolError("browser_extract cannot access hosts resolving to private or internal addresses")


def _is_http_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def _is_blocked_host(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return _is_blocked_ip(normalized)
    except ValueError:
        return False


def _is_blocked_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
