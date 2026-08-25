from __future__ import annotations

import ipaddress
import json
import os
import socket
from typing import Any
from urllib.parse import urlparse


class BrowserToolError(ValueError):
    pass


def build_browser_extract_tool(*, proxy: str = "", timeout_ms: int = 60000) -> Any:
    from langchain_core.tools import StructuredTool

    async def browser_extract(url: str, include_links: bool = True, max_chars: int = 12000) -> str:
        """Open a public web page in a headless browser and extract rendered text."""
        _validate_public_url(url)
        max_chars = max(1000, min(int(max_chars or 12000), 50000))
        navigation_timeout_ms = max(1000, int(timeout_ms or 60000))
        try:
            from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("browser_extract requires langchain-community and playwright") from exc

        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": ["--disable-dev-shm-usage", "--no-sandbox"],
        }
        proxy_url = _resolve_proxy(proxy)
        if proxy_url:
            launch_kwargs["proxy"] = {"server": proxy_url}
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(**launch_kwargs)
        try:
            browser_context = await browser.new_context()
            browser_context.set_default_timeout(navigation_timeout_ms)
            browser_context.set_default_navigation_timeout(navigation_timeout_ms)
            page = await browser_context.new_page()
            page.set_default_timeout(navigation_timeout_ms)
            page.set_default_navigation_timeout(navigation_timeout_ms)
            toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
            tools = {tool.name: tool for tool in toolkit.get_tools()}
            navigate = tools["navigate_browser"]
            extract_text = tools["extract_text"]
            extract_links = tools.get("extract_hyperlinks")

            status = await navigate.ainvoke({"url": url})
            text = await extract_text.ainvoke({})
            links: list[str] = []
            if include_links and extract_links is not None:
                raw_links = await extract_links.ainvoke({"absolute_urls": True})
                parsed_links = json.loads(raw_links)
                if isinstance(parsed_links, list):
                    links = [str(link) for link in parsed_links if _is_http_url(str(link))][:100]

            return json.dumps(
                {
                    "url": url,
                    "status": status,
                    "text": str(text)[:max_chars],
                    "truncated": len(str(text)) > max_chars,
                    "links": links,
                },
                ensure_ascii=False,
            )
        finally:
            await browser.close()
            await playwright.stop()

    return StructuredTool.from_function(
        coroutine=browser_extract,
        name="browser_extract",
        description=(
            "Open a public http/https web page in a headless Playwright browser and extract rendered text and links. "
            "Use this for JavaScript-rendered pages when normal context search is insufficient. "
            "Args: url, include_links=true, max_chars. Private, localhost, and internal network URLs are blocked."
        ),
    )


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
