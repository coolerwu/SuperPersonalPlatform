import asyncio
import json

import httpx
import pytest

from server.domain.tooling import get_tool_definition
from server.infrastructure.browser_tools import (
    BrowserToolError,
    acquire_browser_profile_lock,
    acquire_browser_profile_lock_wait,
    build_browser_extract_tool,
    _http_text_extract,
    _normalize_search_results,
    _resolve_proxy,
    _should_try_http_text_extract,
    _validate_public_url,
)
from server.infrastructure.config import parse_settings
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


def test_browser_extract_is_a_platform_tool(tmp_path) -> None:
    definition = get_tool_definition("browser_extract")
    tools = build_platform_tools(("browser_extract",), context_workspace=tmp_path / "context")

    assert definition.name == "Browser Extract"
    assert [tool.name for tool in tools] == ["browser_extract", "browser_search"]


def test_browser_config_parses_proxy_and_timeout() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "browser": {
                "proxy": "http://127.0.0.1:7890",
                "timeout_ms": 90000,
                "allow_private_hosts": ["finance.wulang.vip", ".internal.test"],
            },
        }
    )

    assert settings.browser.proxy == "http://127.0.0.1:7890"
    assert settings.browser.timeout_ms == 90000
    assert settings.browser.allow_private_hosts == ("finance.wulang.vip", ".internal.test")


def test_browser_proxy_falls_back_to_environment(monkeypatch) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7890")

    assert _resolve_proxy("") == "socks5://127.0.0.1:7890"
    assert _resolve_proxy("http://proxy.example:8080") == "http://proxy.example:8080"


def test_browser_search_normalizes_public_results(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("93.184.216.34", 443))],
    )

    results = _normalize_search_results(
        [
            {"title": "Bing", "url": "https://www.bing.com/search?q=x", "snippet": "ignore"},
            {"title": "Local", "url": "http://127.0.0.1:8888", "snippet": "ignore"},
            {"title": "Example", "url": "https://example.com/a", "snippet": " first  result "},
            {"title": "Example Duplicate", "url": "https://example.com/a", "snippet": "duplicate"},
            {"title": "Second", "url": "https://example.org/b", "snippet": ""},
        ],
        limit=5,
    )

    assert results == [
        {"title": "Example", "url": "https://example.com/a", "snippet": "first result"},
        {"title": "Second", "url": "https://example.org/b", "snippet": ""},
    ]


def test_browser_extract_prefers_http_text_for_raw_github(monkeypatch) -> None:
    captured = {}

    async def fake_http_text_extract(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return json.dumps(
            {
                "url": url,
                "status": "HTTP 200",
                "source": "http_text_fallback",
                "text": "print('ok')",
                "truncated": False,
                "links": [],
            }
        )

    monkeypatch.setattr("server.infrastructure.browser_tools._http_text_extract", fake_http_text_extract)
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("0.0.0.0", 443))],
    )

    tool = build_browser_extract_tool(timeout_ms=30000)
    result = asyncio.run(
        tool.ainvoke(
            {
                "url": "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/tools/code_execution_tool.py",
                "include_links": True,
                "max_chars": 12000,
            }
        )
    )

    payload = json.loads(result)
    assert payload["source"] == "http_text_fallback"
    assert payload["text"] == "print('ok')"
    assert captured["timeout_ms"] == 30000


def test_http_text_extract_reads_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://raw.githubusercontent.com/example/repo/main/tool.py"
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="def run():\n    return 'ok'\n",
        )

    result = asyncio.run(
        _http_text_extract(
            "https://raw.githubusercontent.com/example/repo/main/tool.py",
            include_links=True,
            max_chars=1000,
            timeout_ms=30000,
            transport=httpx.MockTransport(handler),
        )
    )

    payload = json.loads(result)
    assert payload["source"] == "http_text_fallback"
    assert payload["status"] == "HTTP 200"
    assert "def run" in payload["text"]
    assert payload["links"] == []


def test_http_text_extract_collects_html_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<html><body><a href="/a">A</a><a href="https://example.org/b">B</a></body></html>',
        )

    result = asyncio.run(
        _http_text_extract(
            "https://example.com/index.html",
            include_links=True,
            max_chars=1000,
            timeout_ms=30000,
            transport=httpx.MockTransport(handler),
        )
    )

    payload = json.loads(result)
    assert payload["links"] == ["https://example.com/a", "https://example.org/b"]


def test_should_try_http_text_extract_for_raw_and_code_urls() -> None:
    assert _should_try_http_text_extract("https://raw.githubusercontent.com/org/repo/main/file")
    assert _should_try_http_text_extract("https://example.com/source.py")
    assert not _should_try_http_text_extract("https://example.com/article")


def test_browser_extract_receives_agent_profile_context(tmp_path, monkeypatch) -> None:
    captured = {"extract": {}, "search": {}}

    def fake_build_browser_extract_tool(**kwargs):
        captured["extract"].update(kwargs)

        class Tool:
            name = "browser_extract"

        return Tool()

    def fake_build_browser_search_tool(**kwargs):
        captured["search"].update(kwargs)

        class Tool:
            name = "browser_search"

        return Tool()

    monkeypatch.setattr("server.infrastructure.tool_runtime.build_browser_extract_tool", fake_build_browser_extract_tool)
    monkeypatch.setattr("server.infrastructure.tool_runtime.build_browser_search_tool", fake_build_browser_search_tool)

    tools = build_platform_tools(
        ("browser_extract",),
        context_workspace=tmp_path / "context",
        tool_context=PlatformToolContext(
            run_id="run_1",
            source="wechat",
            agent_id="assistant",
            session_id="session_1",
            metadata={},
        ),
    )

    assert [tool.name for tool in tools] == ["browser_extract", "browser_search"]
    assert captured["extract"]["workspace"] == tmp_path
    assert captured["extract"]["agent_id"] == "assistant"
    assert captured["extract"]["allow_private_hosts"] == ()
    assert captured["search"]["workspace"] == tmp_path
    assert captured["search"]["agent_id"] == "assistant"
    assert captured["search"]["allow_private_hosts"] == ()


def test_browser_extract_passes_configured_private_hosts_to_browser_tools(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
auth:
  token: secret-token
browser:
  allow_private_hosts:
    - finance.wulang.vip
""",
        encoding="utf-8",
    )
    captured = {"extract": {}, "search": {}}

    def fake_build_browser_extract_tool(**kwargs):
        captured["extract"].update(kwargs)

        class Tool:
            name = "browser_extract"

        return Tool()

    def fake_build_browser_search_tool(**kwargs):
        captured["search"].update(kwargs)

        class Tool:
            name = "browser_search"

        return Tool()

    monkeypatch.setattr("server.infrastructure.tool_runtime.build_browser_extract_tool", fake_build_browser_extract_tool)
    monkeypatch.setattr("server.infrastructure.tool_runtime.build_browser_search_tool", fake_build_browser_search_tool)

    tools = build_platform_tools(
        ("browser_extract",),
        context_workspace=tmp_path / "context",
        tool_context=PlatformToolContext(
            run_id="run_1",
            source="wechat",
            agent_id="assistant",
            session_id="session_1",
            metadata={},
        ),
    )

    assert [tool.name for tool in tools] == ["browser_extract", "browser_search"]
    assert captured["extract"]["allow_private_hosts"] == ("finance.wulang.vip",)
    assert captured["search"]["allow_private_hosts"] == ("finance.wulang.vip",)


def test_browser_profile_lock_waits_for_same_agent_profile(tmp_path) -> None:
    profile_dir = tmp_path / "browser_profiles" / "assistant"
    first_lock = acquire_browser_profile_lock(
        profile_dir,
        owner="first",
        agent_id="assistant",
        purpose="browser_extract",
    )
    payload = json.loads((profile_dir / "profile.lock.json").read_text(encoding="utf-8"))
    assert payload["pid"] > 0

    async def run_waiter():
        task = asyncio.create_task(
            acquire_browser_profile_lock_wait(
                profile_dir,
                owner="second",
                agent_id="assistant",
                purpose="browser_search",
                timeout_ms=1000,
                poll_seconds=0.01,
            )
        )
        await asyncio.sleep(0.05)
        first_lock.release()
        second_lock = await task
        second_payload = json.loads((profile_dir / "profile.lock.json").read_text(encoding="utf-8"))
        assert second_payload["owner"] == "second"
        second_lock.release()

    asyncio.run(run_waiter())


def test_browser_profile_lock_clears_dead_owner_pid(tmp_path, monkeypatch) -> None:
    profile_dir = tmp_path / "browser_profiles" / "assistant"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.lock.json").write_text(
        json.dumps({"owner": "dead", "agent_id": "assistant", "purpose": "browser_extract", "pid": 999999}),
        encoding="utf-8",
    )

    def fake_kill(pid, signal):
        raise ProcessLookupError(pid)

    monkeypatch.setattr("server.infrastructure.browser_tools.os.kill", fake_kill)

    lock = acquire_browser_profile_lock(
        profile_dir,
        owner="live",
        agent_id="assistant",
        purpose="browser_extract",
    )

    assert json.loads((profile_dir / "profile.lock.json").read_text(encoding="utf-8"))["owner"] == "live"
    lock.release()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8888",
        "http://127.0.0.1:8888",
        "http://192.168.1.3",
        "http://0.0.0.0",
        "file:///etc/passwd",
        "/relative/path",
    ],
)
def test_browser_extract_rejects_local_and_non_http_urls(url) -> None:
    with pytest.raises(BrowserToolError):
        _validate_public_url(url)


def test_browser_extract_private_dns_error_includes_host_and_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("192.168.1.3", 443))],
    )

    with pytest.raises(BrowserToolError) as exc_info:
        _validate_public_url("https://example.test/page")

    assert "host=example.test" in str(exc_info.value)
    assert "address=192.168.1.3" in str(exc_info.value)


def test_browser_extract_skips_local_dns_private_check_when_proxy_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("192.168.1.3", 443))],
    )

    _validate_public_url("https://example.test/page", proxy="socks5://127.0.0.1:7890")


def test_browser_extract_allows_unspecified_dns_for_public_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("0.0.0.0", 443))],
    )

    _validate_public_url("https://raw.githubusercontent.com/example/repo/main/file.txt")


def test_browser_extract_allows_configured_private_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        "server.infrastructure.browser_tools.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, "", ("192.168.1.3", 443))],
    )

    with pytest.raises(BrowserToolError):
        _validate_public_url("https://finance.wulang.vip/page")

    _validate_public_url("https://finance.wulang.vip/page", allow_private_hosts=("finance.wulang.vip",))
    _validate_public_url("https://sub.wulang.vip/page", allow_private_hosts=(".wulang.vip",))
