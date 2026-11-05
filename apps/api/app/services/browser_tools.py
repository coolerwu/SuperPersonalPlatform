from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlsplit

from langchain_core.tools import BaseTool, StructuredTool

from app.config import settings
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext, SkillToolRegistry

logger = logging.getLogger(__name__)

BROWSER_TOOL_NAMES = frozenset(
    {
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_screenshot",
        "browser_close",
    }
)

DEFAULT_TAB = "main"
MAX_BROWSER_URL_LENGTH = 8192
MAX_FILL_CHARS = 10_000
MAX_LABEL_CHARS = 120
MAX_VALUE_CHARS = 120
MAX_HREF_CHARS = 300
DNS_TIMEOUT_SECONDS = 5.0
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
TAB_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
REF_PATTERN = re.compile(r"^s[1-9]\d{0,4}e[1-9]\d{0,3}$")
KEY_PATTERN = re.compile(r"^[A-Za-z0-9+_-]{1,32}$")

VERIFICATION_URL_MARKERS = (
    "wappass.baidu.com",
    "/captcha",
    "/challenge",
    "challenge-platform",
    "/cdn-cgi/challenge",
    "/sorry",
    "verify.",
    "/website-login/error",
    "error_code=300012",
)
VERIFICATION_TITLE_MARKERS = (
    "安全验证",
    "安全限制",
    "验证码",
    "captcha",
    "verify you are human",
    "just a moment",
    "attention required",
    "unusual traffic",
)

NORMAL_USER_AGENT_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{version} Safari/537.36"
)
WEBDRIVER_MASK_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)
MAX_SNAPSHOT_WAIT_SECONDS = 60


class BrowserToolError(Exception):
    """A browser failure whose message is safe and useful to show the model."""


HostResolver = Callable[[str, int], Awaitable[list[str]]]


def playwright_installed() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or not address.is_global
    )


def _bypass_suffix(entry: str) -> str:
    """Reduce one NO_PROXY entry to a lowercase host suffix."""
    value = entry.strip().lower()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.split("/", 1)[0]
    if value.startswith("*."):
        value = value[1:]
    value = value.lstrip(".")
    if value.count(":") == 1:
        name, _, port = value.partition(":")
        if port.isdigit():
            value = name
    return value


def is_bypassed_host(host: str, entries: list[str] | tuple[str, ...]) -> bool:
    """Match a host against NO_PROXY entries (suffix semantics, '*' for all)."""
    value = host.strip().lower().rstrip(".")
    for entry in entries:
        if entry.strip() == "*":
            return True
        suffix = _bypass_suffix(entry)
        if suffix and (value == suffix or value.endswith(f".{suffix}")):
            return True
    return False


def _check_literal_host(hostname: str) -> None:
    host = hostname.lower().rstrip(".")
    if not host:
        raise BrowserToolError("url must include a host")
    if host == "localhost" or any(host.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES):
        raise BrowserToolError("local and private hostnames are not allowed")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return
    if not is_public_ip(host):
        raise BrowserToolError("private, loopback, or link-local addresses are not allowed")


def validate_browser_url_syntax(url: str) -> str:
    value = str(url or "").strip()
    if not value or len(value) > MAX_BROWSER_URL_LENGTH:
        raise BrowserToolError(
            f"url must be non-empty and at most {MAX_BROWSER_URL_LENGTH} characters"
        )
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on malformed or out-of-range ports
    except ValueError as exc:
        raise BrowserToolError(f"invalid url: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BrowserToolError("url must be an HTTP/HTTPS URL with a host")
    if parts.username or parts.password:
        raise BrowserToolError("url must not contain a username or password")
    _check_literal_host(parts.hostname)
    return value


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def _resolve_public_addresses(
    host: str, port: int, resolver: HostResolver | None
) -> list[str]:
    try:
        addresses = await asyncio.wait_for(
            (resolver or _system_resolver)(host, port),
            timeout=DNS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise BrowserToolError(f"could not resolve host {host!r}: timeout") from exc
    except BrowserToolError:
        raise
    except Exception as exc:
        raise BrowserToolError(f"could not resolve host {host!r}: {_short_error(exc)}") from exc
    if not addresses:
        raise BrowserToolError(f"could not resolve host {host!r}")
    if any(not is_public_ip(address) for address in addresses):
        raise BrowserToolError(f"host {host!r} resolves to a private or reserved address")
    return addresses


async def validate_public_url(url: str, *, resolver: HostResolver | None = None) -> str:
    value = validate_browser_url_syntax(url)
    parts = urlsplit(value)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    await _resolve_public_addresses(host, port, resolver)
    return value


def normalize_ref(value: str) -> str:
    ref = str(value or "").strip().strip("[]")
    if ref.startswith("ref="):
        ref = ref[4:].strip()
    if not REF_PATTERN.match(ref):
        raise BrowserToolError(
            f"invalid ref {value!r}; call browser_snapshot and copy a ref like s1e3"
        )
    return ref


def normalize_tab_name(value: str | None) -> str:
    name = str(value or "").strip()
    if not name:
        return DEFAULT_TAB
    if not TAB_NAME_PATTERN.match(name):
        raise BrowserToolError("tab must be 1-32 letters, digits, underscores, or dashes")
    return name


def validate_press_key(value: str) -> str:
    key = str(value or "").strip()
    if not KEY_PATTERN.match(key):
        raise BrowserToolError(f"unsupported key {value!r}")
    return key


@dataclass(frozen=True)
class SnapshotElement:
    ref: str
    tag: str
    type: str = ""
    role: str = ""
    label: str = ""
    name: str = ""
    value: str = ""
    href: str = ""
    checked: bool = False
    disabled: bool = False
    options: int = 0


@dataclass(frozen=True)
class SnapshotData:
    title: str
    url: str
    elements: tuple[SnapshotElement, ...]
    total_elements: int
    body_text: str
    body_truncated: bool = False


SNAPSHOT_SCRIPT = r"""
({ generation, maxElements, maxTextChars }) => {
  document.querySelectorAll('[data-sp-ref]').forEach((el) => el.removeAttribute('data-sp-ref'));

  const selector = [
    'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
    'summary', '[contenteditable="true"]', '[contenteditable=""]',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="switch"]', '[role="tab"]', '[role="menuitem"]', '[role="option"]',
    '[role="combobox"]', '[role="textbox"]', '[role="searchbox"]', '[onclick]'
  ].join(', ');

  const clean = (value, limit) => {
    const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  };

  const isVisible = (el) => {
    if (el.closest('[hidden], [aria-hidden="true"]')) return false;
    const style = window.getComputedStyle(el);
    if (
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      style.visibility === 'collapse' ||
      style.opacity === '0'
    ) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 2 && rect.height >= 2;
  };

  const visible = Array.from(new Set(document.querySelectorAll(selector))).filter(isVisible);
  const elements = visible.slice(0, maxElements).map((el, index) => {
    const ref = `s${generation}e${index + 1}`;
    el.setAttribute('data-sp-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const label = clean(
      el.getAttribute('aria-label') ||
        el.getAttribute('placeholder') ||
        el.getAttribute('title') ||
        el.getAttribute('alt') ||
        el.innerText ||
        el.textContent ||
        el.getAttribute('name') ||
        '',
      200
    );
    return {
      ref,
      tag,
      type,
      role: el.getAttribute('role') || '',
      name: clean(el.getAttribute('name') || '', 100),
      label,
      href: el.href ? String(el.href).slice(0, 300) : '',
      value: type === 'password' ? '' : clean(el.value == null ? '' : el.value, 200),
      checked: !!(el.checked || el.getAttribute('aria-checked') === 'true'),
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      options: tag === 'select' ? el.options.length : 0
    };
  });

  const rawText = document.body ? (document.body.innerText || '') : '';
  const bodyText = rawText.slice(0, maxTextChars);
  return {
    title: document.title || '',
    url: String(location.href),
    elements,
    totalElements: visible.length,
    bodyText,
    bodyTruncated: rawText.length > bodyText.length
  };
}
"""


def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return f"{text[:limit]}…" if len(text) > limit else text


def _quote(value: Any, limit: int) -> str:
    return json.dumps(_clean(value, limit), ensure_ascii=False)


def parse_snapshot(raw: dict[str, Any]) -> SnapshotData:
    elements: list[SnapshotElement] = []
    for item in raw.get("elements") or []:
        if not isinstance(item, dict):
            continue
        elements.append(
            SnapshotElement(
                ref=str(item.get("ref") or ""),
                tag=str(item.get("tag") or ""),
                type=str(item.get("type") or ""),
                role=str(item.get("role") or ""),
                label=str(item.get("label") or ""),
                name=str(item.get("name") or ""),
                value=str(item.get("value") or ""),
                href=str(item.get("href") or ""),
                checked=bool(item.get("checked")),
                disabled=bool(item.get("disabled")),
                options=int(item.get("options") or 0),
            )
        )
    return SnapshotData(
        title=str(raw.get("title") or ""),
        url=str(raw.get("url") or ""),
        elements=tuple(elements),
        total_elements=int(raw.get("totalElements") or len(elements)),
        body_text=str(raw.get("bodyText") or ""),
        body_truncated=bool(raw.get("bodyTruncated")),
    )


def _implicit_role(element: SnapshotElement) -> str:
    if element.tag == "input" and element.type:
        return f"input type={element.type}"
    if element.role:
        return element.role
    if element.tag == "a" and element.href:
        return "link"
    return element.tag or "element"


def format_snapshot(
    data: SnapshotData,
    *,
    max_elements: int,
    max_text_chars: int,
    max_snapshot_chars: int,
) -> str:
    lines = [
        f"Page: {_clean(data.title, 200) or '(untitled)'}",
        f"URL: {_clean(data.url, MAX_HREF_CHARS * 2)}",
    ]
    shown = min(len(data.elements), max_elements)
    marker = " (showing first {})".format(shown) if data.total_elements > shown else ""
    lines.append(f"Interactive elements: {shown}/{data.total_elements}{marker}")
    for element in data.elements[:max_elements]:
        line = f"[ref={element.ref}] {_implicit_role(element)}"
        if element.label:
            line += f" {_quote(element.label, MAX_LABEL_CHARS)}"
        if element.name and element.tag in ("input", "select", "textarea"):
            line += f" name={_clean(element.name, 60)}"
        if element.value and element.type != "password":
            line += f" value={_quote(element.value, MAX_VALUE_CHARS)}"
        if element.href:
            line += f" -> {_clean(element.href, MAX_HREF_CHARS)}"
        if element.checked:
            line += " (checked)"
        if element.disabled:
            line += " (disabled)"
        if element.options:
            line += f" ({element.options} options)"
        lines.append(line)
    if data.total_elements > shown:
        lines.append(f"... [{data.total_elements - shown} more elements not shown]")
    lines.append("--- page text ---")
    if max_text_chars >= 0:
        lines.append(data.body_text[:max_text_chars] or "(empty)")
    else:
        lines.append(data.body_text or "(empty)")
    if data.body_truncated:
        lines.append("... [page text truncated]")
    output = "\n".join(lines)
    if len(output) > max_snapshot_chars:
        output = f"{output[:max_snapshot_chars]}\n... [snapshot truncated]"
    return output


def _short_error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300] or type(exc).__name__


def _cookie_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("name") or ""),
        str(item.get("domain") or ""),
        str(item.get("path") or "/"),
    )


def _merge_cookies(*groups: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            key = _cookie_key(item)
            if not key[0] or not key[1]:
                continue
            merged[key] = item
    return list(merged.values())


def _merge_origins(*groups: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            origin = str(item.get("origin") or "")
            if not origin:
                continue
            target = merged.setdefault(origin, {"origin": origin, "localStorage": []})
            entries = {
                str(entry.get("name") or ""): entry
                for entry in target.get("localStorage") or []
                if isinstance(entry, dict) and entry.get("name")
            }
            for entry in item.get("localStorage") or []:
                if isinstance(entry, dict) and entry.get("name"):
                    entries[str(entry["name"])] = entry
            target["localStorage"] = list(entries.values())
            for key, value in item.items():
                if key not in ("origin", "localStorage"):
                    target[key] = value
    return list(merged.values())


def merge_storage_state(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge Playwright storage states; incoming cookies and localStorage win."""
    base = existing if isinstance(existing, dict) else {}
    return {
        "cookies": _merge_cookies(base.get("cookies"), incoming.get("cookies")),
        "origins": _merge_origins(base.get("origins"), incoming.get("origins")),
    }


def verification_reason(
    url: str,
    title: str,
    *,
    total_elements: int | None = None,
    body_text: str = "",
) -> str | None:
    """Detect anti-bot verification pages so the model stops instead of looping."""
    lowered_url = str(url or "").lower()
    for marker in VERIFICATION_URL_MARKERS:
        if marker in lowered_url:
            return f"anti-bot verification page detected at {url}"

    lowered_title = str(title or "").lower()
    if not any(marker in lowered_title for marker in VERIFICATION_TITLE_MARKERS):
        return None
    if total_elements is not None and total_elements > 5:
        return None
    if total_elements is not None and len(body_text) > 2_000:
        return None
    return f"anti-bot verification page detected ({title!r})"


def _verification_error(reason: str) -> BrowserToolError:
    return BrowserToolError(
        f"{reason}; stop and tell the user, then try a mobile or lighter search endpoint "
        "or another source; never attempt to bypass the verification"
    )


@dataclass
class _Tab:
    name: str
    page: Any
    lock: asyncio.Lock
    generation: int = 0


class BrowserSession:
    """One browser + context per Run, with a bounded number of live tabs."""

    def __init__(
        self,
        *,
        concurrency: int | None = None,
        navigation_timeout_ms: int | None = None,
        action_timeout_ms: int | None = None,
        settle_ms: int | None = None,
        max_elements: int | None = None,
        max_text_chars: int | None = None,
        max_snapshot_chars: int | None = None,
        screenshot_max_bytes: int | None = None,
        no_sandbox: bool | None = None,
        normal_identity: bool | None = None,
        user_agent: str | None = None,
        locale: str | None = None,
        timezone: str | None = None,
        accept_language: str | None = None,
        persist_state: bool | None = None,
        state_path: str | Path | None = None,
        proxy_url: str | None = None,
        proxy_bypass: str | None = None,
        resolver: HostResolver | None = None,
        start_playwright: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self.concurrency = max(
            1, int(concurrency if concurrency is not None else settings.browser_concurrency)
        )
        self.navigation_timeout_ms = max(
            1_000,
            int(
                navigation_timeout_ms
                if navigation_timeout_ms is not None
                else settings.browser_navigation_timeout_ms
            ),
        )
        self.action_timeout_ms = max(
            500,
            int(
                action_timeout_ms
                if action_timeout_ms is not None
                else settings.browser_action_timeout_ms
            ),
        )
        self.settle_ms = max(
            0, int(settle_ms if settle_ms is not None else settings.browser_settle_ms)
        )
        self.max_elements = max(
            10, int(max_elements if max_elements is not None else settings.browser_max_elements)
        )
        self.max_text_chars = max(
            500,
            int(max_text_chars if max_text_chars is not None else settings.browser_max_text_chars),
        )
        self.max_snapshot_chars = max(
            2_000,
            int(
                max_snapshot_chars
                if max_snapshot_chars is not None
                else settings.browser_max_snapshot_chars
            ),
        )
        self.max_screenshot_bytes = max(
            100_000,
            int(
                screenshot_max_bytes
                if screenshot_max_bytes is not None
                else settings.browser_screenshot_max_bytes
            ),
        )
        self.no_sandbox = bool(
            no_sandbox if no_sandbox is not None else settings.browser_no_sandbox
        )
        self.normal_identity = bool(
            normal_identity
            if normal_identity is not None
            else settings.browser_normal_identity
        )
        self.user_agent = str(
            user_agent if user_agent is not None else settings.browser_user_agent
        ).strip()
        self.locale = str(locale if locale is not None else settings.browser_locale)
        self.timezone = str(
            timezone if timezone is not None else settings.browser_timezone
        )
        self.accept_language = str(
            accept_language
            if accept_language is not None
            else settings.browser_accept_language
        )
        self.persist_state = bool(
            persist_state
            if persist_state is not None
            else settings.browser_persist_state
        )
        self.state_path = (
            Path(state_path).expanduser()
            if state_path is not None
            else settings.browser_storage_state_path
        )
        self._state_digest: str | None = None
        self._resolver = resolver
        self._start_playwright = start_playwright

        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._start_lock = asyncio.Lock()
        self._start_error: str | None = None
        self._closed = False
        self._creating_page = 0

        self._tabs: dict[str, _Tab] = {}
        self._tabs_lock = asyncio.Lock()
        self._tab_slots = asyncio.Semaphore(self.concurrency)
        self._checked_hosts: set[str] = set()

        proxy_value = (
            proxy_url
            if proxy_url is not None
            else (settings.https_proxy or settings.http_proxy or "")
        )
        self.proxy_url = str(proxy_value or "").strip()
        bypass_value = (
            proxy_bypass if proxy_bypass is not None else (settings.no_proxy or "")
        )
        self.proxy_bypass = str(bypass_value or "").strip()
        self._bypass_entries = tuple(
            entry.strip() for entry in self.proxy_bypass.split(",") if entry.strip()
        )

    def _playwright_proxy(self) -> dict[str, str] | None:
        """Chromium ignores HTTP_PROXY; hand it the proxy explicitly."""
        server = self.proxy_url
        if not server:
            return None
        if "://" not in server:
            server = f"http://{server}"
        try:
            parts = urlsplit(server)
            host = parts.hostname or ""
            port = parts.port
        except ValueError:
            logger.warning("invalid browser proxy url; browsing without it")
            return None
        if not host:
            logger.warning("invalid browser proxy url; browsing without it")
            return None
        if ":" in host:
            host = f"[{host}]"
        netloc = host if port is None else f"{host}:{port}"
        proxy = {"server": f"{parts.scheme or 'http'}://{netloc}"}
        if parts.username:
            proxy["username"] = unquote(parts.username)
            proxy["password"] = unquote(parts.password or "")
        bypass = self._playwright_bypass()
        if bypass:
            proxy["bypass"] = bypass
        return proxy

    def _playwright_bypass(self) -> str:
        values: list[str] = []
        for entry in self._bypass_entries:
            if entry == "*":
                values.append("*")
                continue
            suffix = _bypass_suffix(entry)
            if suffix:
                values.extend((suffix, f".{suffix}"))
        return ",".join(dict.fromkeys(values))

    def _uses_proxy(self, host: str) -> bool:
        return bool(self.proxy_url) and not is_bypassed_host(host, self._bypass_entries)

    async def _ensure_started(self) -> None:
        if self._browser is not None:
            return
        if self._start_error is not None:
            raise BrowserToolError(self._start_error)
        async with self._start_lock:
            if self._browser is not None:
                return
            if self._start_error is not None:
                raise BrowserToolError(self._start_error)
            playwright: Any = None
            browser: Any = None
            try:
                if self._start_playwright is not None:
                    playwright = await self._start_playwright()
                else:
                    from playwright.async_api import async_playwright

                    playwright = await async_playwright().start()
                args = ["--no-sandbox"] if self.no_sandbox else []
                if self.normal_identity:
                    # Sites such as xiaohongshu redirect Playwright's default
                    # headless client to an anti-bot page; a regular desktop
                    # profile reaches the normal login page instead.
                    args.append("--disable-blink-features=AutomationControlled")
                launch_options: dict[str, Any] = {"headless": True, "args": args}
                proxy = self._playwright_proxy()
                if proxy is not None:
                    launch_options["proxy"] = proxy
                browser = await playwright.chromium.launch(**launch_options)
                context_options: dict[str, Any] = {
                    "accept_downloads": False,
                    "viewport": {"width": 1280, "height": 900},
                }
                if self.normal_identity:
                    context_options.update(
                        user_agent=self._user_agent(browser),
                        locale=self.locale,
                        timezone_id=self.timezone,
                        extra_http_headers={"Accept-Language": self.accept_language},
                    )
                storage_state = self._load_storage_state()
                if storage_state is not None:
                    context_options["storage_state"] = storage_state
                try:
                    context = await browser.new_context(**context_options)
                except Exception:
                    if "storage_state" not in context_options:
                        raise
                    # A corrupt or outdated state file must not break browsing.
                    logger.warning(
                        "browser state rejected by Chromium; starting clean", exc_info=True
                    )
                    context_options.pop("storage_state", None)
                    context = await browser.new_context(**context_options)
                if self.normal_identity:
                    await context.add_init_script(WEBDRIVER_MASK_SCRIPT)
                context.set_default_timeout(self.action_timeout_ms)
                context.set_default_navigation_timeout(self.navigation_timeout_ms)
                await context.route("**/*", self._handle_route)
                context.on("page", self._on_page)
            except ImportError as exc:
                self._start_error = (
                    "Playwright is not installed; run: pip install playwright && "
                    "playwright install chromium"
                )
                raise BrowserToolError(self._start_error) from exc
            except Exception as exc:
                await self._stop_partial(playwright, browser)
                self._start_error = (
                    f"Chromium is not available for Playwright ({_short_error(exc)}); "
                    "run: playwright install chromium"
                )
                raise BrowserToolError(self._start_error) from exc
            self._playwright = playwright
            self._browser = browser
            self._context = context

    def _user_agent(self, browser: Any) -> str:
        if self.user_agent:
            return self.user_agent
        version = str(getattr(browser, "version", "") or "141.0.0.0")
        return NORMAL_USER_AGENT_TEMPLATE.format(version=version)

    def _load_storage_state(self) -> dict[str, Any] | None:
        """Load cookies/localStorage saved by an earlier Run."""
        if not self.persist_state:
            return None
        try:
            raw = self.state_path.read_bytes()
        except FileNotFoundError:
            self._state_digest = None
            return None
        except OSError:
            logger.warning(
                "browser state read failed: %s", self.state_path, exc_info=True
            )
            return None
        self._state_digest = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            logger.warning(
                "browser state file is not valid JSON: %s", self.state_path, exc_info=True
            )
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("cookies"), list):
            return None
        return payload

    async def _save_storage_state(self) -> None:
        """Persist the context's cookies/localStorage for later Runs.

        The state loaded at startup is overwritten wholesale. If another Run
        wrote the file while this one was active, the two states are merged so
        a fresh login in either session is not lost.
        """
        if not self.persist_state or self._context is None:
            return
        try:
            incoming = await self._context.storage_state()
        except Exception:
            logger.warning("browser state snapshot failed", exc_info=True)
            return
        if not isinstance(incoming, dict):
            return

        existing: dict[str, Any] | None = None
        existing_digest: str | None = None
        try:
            raw = self.state_path.read_bytes()
            existing_digest = hashlib.sha256(raw).hexdigest()
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                existing = payload
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, ValueError):
            logger.warning(
                "browser state file unreadable; replacing: %s", self.state_path, exc_info=True
            )
        if existing is not None and existing_digest != self._state_digest:
            merged = merge_storage_state(existing, incoming)
        else:
            merged = merge_storage_state(None, incoming)

        temp_name = ""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                prefix=".tmp-", suffix=".json", dir=self.state_path.parent
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(merged, stream, ensure_ascii=False)
            os.replace(temp_name, self.state_path)
        except OSError:
            logger.warning(
                "browser state write failed: %s", self.state_path, exc_info=True
            )
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    async def _stop_partial(self, playwright: Any, browser: Any) -> None:
        for closer in (
            getattr(browser, "close", None),
            getattr(playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer(), timeout=5)
            except Exception:
                pass

    def _on_page(self, page: Any) -> None:
        if self._creating_page:
            return
        asyncio.create_task(self._close_popup(page))

    async def _close_popup(self, page: Any) -> None:
        try:
            await page.close()
        except Exception:
            pass

    async def _handle_route(self, route: Any) -> None:
        request = route.request
        url = str(request.url)
        try:
            if url.startswith(("http://", "https://")):
                parts = urlsplit(url)
                host = (parts.hostname or "").lower()
                port = parts.port or (443 if parts.scheme == "https" else 80)
                await self._check_host(host, port)
            elif url.startswith("file:"):
                raise BrowserToolError("file:// requests are not allowed")
            elif request.is_navigation_request() and not url.startswith(
                ("about:blank", "data:", "blob:")
            ):
                raise BrowserToolError(f"navigation scheme is not allowed: {url[:80]}")
        except BrowserToolError as exc:
            logger.info("blocked browser request %s: %s", url, exc)
            await route.abort()
            return
        except Exception:
            await route.abort()
            return
        await route.continue_()

    async def _check_host(self, host: str, port: int) -> None:
        _check_literal_host(host)
        if host in self._checked_hosts:
            return
        if self._uses_proxy(host):
            # Chromium resolves through the proxy, so a poisoned local
            # resolver must not decide whether the request is allowed.
            self._checked_hosts.add(host)
            return
        await _resolve_public_addresses(host, port, self._resolver)
        self._checked_hosts.add(host)

    async def _get_or_create_tab(self, name: str | None, *, create: bool) -> _Tab:
        key = normalize_tab_name(name)
        async with self._tabs_lock:
            tab = self._tabs.get(key)
            if tab is not None:
                is_closed = getattr(tab.page, "is_closed", None)
                if is_closed is not None and is_closed():
                    self._tabs.pop(key, None)
                    self._tab_slots.release()
                else:
                    return tab
            if not create:
                raise BrowserToolError(
                    f"tab {key!r} is not open; call browser_open(url=..., tab={key!r})"
                )
            if self._tab_slots.locked():
                raise BrowserToolError(
                    f"tab limit reached ({self.concurrency}); "
                    "call browser_close(tab=...) before opening another tab"
                )
            await self._tab_slots.acquire()
            try:
                await self._ensure_started()
                self._creating_page += 1
                try:
                    page = await self._context.new_page()
                finally:
                    self._creating_page -= 1
                tab = _Tab(name=key, page=page, lock=asyncio.Lock())
                self._tabs[key] = tab
                return tab
            except BaseException:
                self._tab_slots.release()
                raise

    async def _settle(self, page: Any) -> None:
        if self.settle_ms:
            await page.wait_for_timeout(self.settle_ms)

    async def _wait_after_action(self, page: Any) -> None:
        for _ in range(2):
            try:
                await page.wait_for_load_state(
                    "load", timeout=min(2_000, self.navigation_timeout_ms)
                )
                break
            except Exception:
                await asyncio.sleep(0.2)
        await self._settle(page)

    async def _take_snapshot(self, tab: _Tab) -> str:
        tab.generation += 1
        payload = {
            "generation": tab.generation,
            "maxElements": self.max_elements,
            "maxTextChars": self.max_text_chars,
        }
        try:
            raw = await tab.page.evaluate(SNAPSHOT_SCRIPT, payload)
        except Exception as first:
            if "execution context was destroyed" not in _short_error(first).lower():
                raise BrowserToolError(f"snapshot failed: {_short_error(first)}") from first
            await asyncio.sleep(0.2)
            tab.generation += 1
            payload["generation"] = tab.generation
            try:
                raw = await tab.page.evaluate(SNAPSHOT_SCRIPT, payload)
            except Exception as second:
                raise BrowserToolError(f"snapshot failed: {_short_error(second)}") from second
        data = parse_snapshot(raw if isinstance(raw, dict) else {})
        reason = verification_reason(
            data.url,
            data.title,
            total_elements=data.total_elements,
            body_text=data.body_text,
        )
        if reason is not None:
            raise _verification_error(reason)
        return format_snapshot(
            data,
            max_elements=self.max_elements,
            max_text_chars=self.max_text_chars,
            max_snapshot_chars=self.max_snapshot_chars,
        )

    async def _action_result(self, header: str, tab: _Tab) -> str:
        try:
            snapshot = await self._take_snapshot(tab)
        except BrowserToolError as exc:
            return f"{header}\n\nSnapshot unavailable: {exc}"
        return f"{header}\n\n{snapshot}"

    async def open(self, url: str, tab: str = DEFAULT_TAB) -> str:
        target = validate_browser_url_syntax(url)
        parts = urlsplit(target)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        await self._check_host(host, port)
        tab_state = await self._get_or_create_tab(tab, create=True)
        async with tab_state.lock:
            try:
                response = await asyncio.wait_for(
                    tab_state.page.goto(
                        target,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    ),
                    timeout=self.navigation_timeout_ms / 1000 + 5,
                )
            except TimeoutError as exc:
                raise BrowserToolError(
                    f"navigation timed out after {self.navigation_timeout_ms} ms"
                ) from exc
            except Exception as exc:
                raise BrowserToolError(f"navigation failed: {_short_error(exc)}") from exc
            try:
                await tab_state.page.wait_for_load_state(
                    "load", timeout=min(5_000, self.navigation_timeout_ms)
                )
            except Exception:
                pass
            await self._settle(tab_state.page)
            title = ""
            try:
                title = await tab_state.page.title()
            except Exception:
                pass
            reason = verification_reason(str(tab_state.page.url), title)
            if reason is not None:
                raise _verification_error(reason)
            status = getattr(response, "status", None) if response is not None else None
            if status in (403, 429):
                raise BrowserToolError(
                    f"request blocked (HTTP {status}); try a mobile or lighter endpoint, "
                    "or another source"
                )
            header = f"Opened {tab_state.page.url} in tab {tab_state.name!r}"
            if status is not None:
                header += f" (HTTP {status})"
            return await self._action_result(header, tab_state)

    async def snapshot(self, tab: str = DEFAULT_TAB, *, wait_seconds: int = 0) -> str:
        tab_state = await self._get_or_create_tab(tab, create=False)
        wait_seconds = max(0, min(int(wait_seconds), MAX_SNAPSHOT_WAIT_SECONDS))
        async with tab_state.lock:
            if wait_seconds:
                await tab_state.page.wait_for_timeout(wait_seconds * 1000)
            return await self._take_snapshot(tab_state)

    async def click(self, ref: str, tab: str = DEFAULT_TAB) -> str:
        normalized = normalize_ref(ref)
        tab_state = await self._get_or_create_tab(tab, create=False)
        async with tab_state.lock:
            locator = tab_state.page.locator(f'[data-sp-ref="{normalized}"]')
            try:
                count = await locator.count()
            except Exception as exc:
                raise BrowserToolError(f"could not inspect ref {normalized}: {_short_error(exc)}")
            if count == 0:
                raise BrowserToolError(
                    f"ref {normalized} is stale or missing; "
                    "call browser_snapshot and use a current ref"
                )
            try:
                await locator.first.click(timeout=self.action_timeout_ms)
            except Exception as exc:
                raise BrowserToolError(f"click failed: {_short_error(exc)}") from exc
            await self._wait_after_action(tab_state.page)
            return await self._action_result(
                f"Clicked {normalized} in tab {tab_state.name!r}", tab_state
            )

    async def fill(self, ref: str, text: str, tab: str = DEFAULT_TAB, *, submit: bool = False) -> str:
        if not isinstance(text, str):
            raise BrowserToolError("text must be a string")
        if len(text) > MAX_FILL_CHARS:
            raise BrowserToolError(f"text must be at most {MAX_FILL_CHARS} characters")
        normalized = normalize_ref(ref)
        tab_state = await self._get_or_create_tab(tab, create=False)
        async with tab_state.lock:
            locator = tab_state.page.locator(f'[data-sp-ref="{normalized}"]')
            try:
                count = await locator.count()
            except Exception as exc:
                raise BrowserToolError(f"could not inspect ref {normalized}: {_short_error(exc)}")
            if count == 0:
                raise BrowserToolError(
                    f"ref {normalized} is stale or missing; "
                    "call browser_snapshot and use a current ref"
                )
            try:
                element = locator.first
                tag = await element.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    try:
                        await element.select_option(label=text, timeout=self.action_timeout_ms)
                    except Exception:
                        await element.select_option(value=text, timeout=self.action_timeout_ms)
                else:
                    await element.fill(text, timeout=self.action_timeout_ms)
                if submit:
                    await tab_state.page.keyboard.press("Enter")
            except Exception as exc:
                raise BrowserToolError(f"fill failed: {_short_error(exc)}") from exc
            await self._wait_after_action(tab_state.page)
            return await self._action_result(
                f"Filled {normalized} in tab {tab_state.name!r}", tab_state
            )

    async def press(self, key: str, tab: str = DEFAULT_TAB) -> str:
        pressed = validate_press_key(key)
        tab_state = await self._get_or_create_tab(tab, create=False)
        async with tab_state.lock:
            try:
                await tab_state.page.keyboard.press(pressed)
            except Exception as exc:
                raise BrowserToolError(f"press failed: {_short_error(exc)}") from exc
            await self._wait_after_action(tab_state.page)
            return await self._action_result(
                f"Pressed {pressed} in tab {tab_state.name!r}", tab_state
            )

    async def screenshot(
        self, tab: str = DEFAULT_TAB, *, full_page: bool = False
    ) -> list[dict[str, Any]]:
        tab_state = await self._get_or_create_tab(tab, create=False)
        async with tab_state.lock:
            try:
                image = await tab_state.page.screenshot(
                    type="png",
                    full_page=full_page,
                    timeout=self.action_timeout_ms,
                )
            except Exception as exc:
                raise BrowserToolError(f"screenshot failed: {_short_error(exc)}") from exc
            url = str(tab_state.page.url)
        if not isinstance(image, (bytes, bytearray)):
            raise BrowserToolError("screenshot returned no image data")
        if len(image) > self.max_screenshot_bytes:
            raise BrowserToolError(
                f"screenshot is {len(image)} bytes, over the "
                f"{self.max_screenshot_bytes}-byte limit; try full_page=false"
            )
        encoded = base64.b64encode(bytes(image)).decode("ascii")
        return [
            {"type": "text", "text": f"Screenshot of tab {tab_state.name!r} at {url}"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
        ]

    async def close_tab(self, tab: str = DEFAULT_TAB) -> str:
        key = normalize_tab_name(tab)
        async with self._tabs_lock:
            tab_state = self._tabs.get(key)
        if tab_state is None:
            raise BrowserToolError(f"tab {key!r} is not open")
        async with tab_state.lock:
            try:
                await asyncio.wait_for(tab_state.page.close(), timeout=5)
            except Exception:
                pass
        async with self._tabs_lock:
            if self._tabs.pop(key, None) is not None:
                self._tab_slots.release()
            remaining = sorted(self._tabs)
        return f"Closed tab {key!r}. Open tabs: {', '.join(remaining) or 'none'}"

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(self._save_storage_state(), timeout=10)
        except Exception:
            logger.warning("browser state save failed", exc_info=True)
        tabs = list(self._tabs.values())
        self._tabs.clear()
        for tab_state in tabs:
            try:
                await asyncio.wait_for(tab_state.page.close(), timeout=5)
            except Exception:
                pass
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer(), timeout=5)
            except Exception:
                pass


def _session_for(context: SkillToolContext) -> BrowserSession:
    session = getattr(context, "browser", None)
    if session is None:
        raise BrowserToolError("browser session is not available for this run")
    return session


async def _call(
    context: SkillToolContext,
    operation: Callable[[BrowserSession], Awaitable[str | list[dict[str, Any]]]],
) -> str | list[dict[str, Any]]:
    try:
        return await operation(_session_for(context))
    except BrowserToolError as exc:
        return f"Error: {exc}"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("browser tool failed")
        return f"Error: browser operation failed: {_short_error(exc)}"


def _make_browser_open_tool(context: SkillToolContext) -> BaseTool:
    async def browser_open(url: str, tab: str = DEFAULT_TAB) -> str:
        """打开网页并返回页面文字与可交互元素引用。"""
        return await _call(context, lambda session: session.open(url, tab))

    return StructuredTool.from_function(
        name="browser_open",
        description=(
            "打开 http/https 网页（仅公开地址），返回标题、正文和可交互元素 ref。"
            "使用普通桌面浏览器身份并复用之前保存的登录态；适合 JS 渲染页面、点击、填写和登录。"
            "默认只返回文字；需要让用户扫码或查看页面外观时调用 browser_screenshot。"
            "tab 默认 main，同一 Run 最多 2 个标签页。"
        ),
        coroutine=browser_open,
    )


def _make_browser_snapshot_tool(context: SkillToolContext) -> BaseTool:
    async def browser_snapshot(tab: str = DEFAULT_TAB, wait_seconds: int = 0) -> str:
        """返回当前页面的最新快照。"""
        return await _call(
            context, lambda session: session.snapshot(tab, wait_seconds=wait_seconds)
        )

    return StructuredTool.from_function(
        name="browser_snapshot",
        description=(
            "读取当前标签页的最新快照（标题、正文、可交互元素 ref）。"
            "每次快照后旧 ref 全部失效，必须使用本次返回的 ref。"
            "wait_seconds 取 0-60，先等待再读取，用于等待用户扫码登录或页面异步加载。"
        ),
        coroutine=browser_snapshot,
    )


def _make_browser_click_tool(context: SkillToolContext) -> BaseTool:
    async def browser_click(ref: str, tab: str = DEFAULT_TAB) -> str:
        """点击快照中的元素并返回新快照。"""
        return await _call(context, lambda session: session.click(ref, tab))

    return StructuredTool.from_function(
        name="browser_click",
        description=(
            "点击 browser_snapshot 返回的 ref（如 s1e3），返回操作后的新快照。"
            "ref 只对最近一次快照有效；失效时先重新快照。"
        ),
        coroutine=browser_click,
    )


def _make_browser_fill_tool(context: SkillToolContext) -> BaseTool:
    async def browser_fill(
        ref: str, text: str, tab: str = DEFAULT_TAB, submit: bool = False
    ) -> str:
        """在输入框中填写文本并返回新快照。"""
        return await _call(context, lambda session: session.fill(ref, text, tab, submit=submit))

    return StructuredTool.from_function(
        name="browser_fill",
        description=(
            "向 browser_snapshot 返回的输入框 ref 填写文本，返回新快照。"
            "submit=true 时在填写后按 Enter。不要输入密钥或密码，除非用户明确要求。"
        ),
        coroutine=browser_fill,
    )


def _make_browser_press_tool(context: SkillToolContext) -> BaseTool:
    async def browser_press(key: str, tab: str = DEFAULT_TAB) -> str:
        """按下键盘按键并返回新快照。"""
        return await _call(context, lambda session: session.press(key, tab))

    return StructuredTool.from_function(
        name="browser_press",
        description="在页面上按下按键（如 Enter、Escape、Tab、Control+A），返回新快照。",
        coroutine=browser_press,
    )


def _make_browser_screenshot_tool(context: SkillToolContext) -> BaseTool:
    async def browser_screenshot(tab: str = DEFAULT_TAB, full_page: bool = False):
        """截取当前标签页并返回 PNG 图片。"""
        return await _call(
            context, lambda session: session.screenshot(tab, full_page=full_page)
        )

    return StructuredTool.from_function(
        name="browser_screenshot",
        description=(
            "截取当前标签页并返回 PNG 图片，同时展示给用户。"
            "用户要求查看截图、页面外观或布局，或需要用户扫码登录时调用；"
            "full_page=true 截取整页。Provider 模型必须支持图片输入。"
        ),
        coroutine=browser_screenshot,
    )


def _make_browser_close_tool(context: SkillToolContext) -> BaseTool:
    async def browser_close(tab: str = DEFAULT_TAB) -> str:
        """关闭标签页并释放并发槽位。"""
        return await _call(context, lambda session: session.close_tab(tab))

    return StructuredTool.from_function(
        name="browser_close",
        description="关闭指定标签页并释放并发槽位；浏览器进程在 Run 结束时自动关闭。",
        coroutine=browser_close,
    )


def register_browser_tools(registry: SkillToolRegistry = SKILL_TOOL_REGISTRY) -> None:
    specs = (
        ("browser_open", _make_browser_open_tool, "打开网页并返回文字快照（普通桌面浏览器身份）"),
        (
            "browser_snapshot",
            _make_browser_snapshot_tool,
            "读取当前标签页的最新文字快照（可等待扫码登录）",
        ),
        ("browser_click", _make_browser_click_tool, "点击快照中的元素"),
        ("browser_fill", _make_browser_fill_tool, "向输入框填写文本"),
        ("browser_press", _make_browser_press_tool, "按下键盘按键"),
        ("browser_screenshot", _make_browser_screenshot_tool, "按需截取当前标签页图片"),
        ("browser_close", _make_browser_close_tool, "关闭标签页"),
    )
    for name, factory, description in specs:
        if registry.has(name):
            continue
        registry.register_factory(name, factory, description=description)


register_browser_tools()
