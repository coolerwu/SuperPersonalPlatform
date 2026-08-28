import pytest

from server.domain.tooling import get_tool_definition
from server.infrastructure.browser_tools import BrowserToolError, _normalize_search_results, _resolve_proxy, _validate_public_url
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
            "browser": {"proxy": "http://127.0.0.1:7890", "timeout_ms": 90000},
        }
    )

    assert settings.browser.proxy == "http://127.0.0.1:7890"
    assert settings.browser.timeout_ms == 90000


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
    assert captured["search"]["workspace"] == tmp_path
    assert captured["search"]["agent_id"] == "assistant"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8888",
        "http://127.0.0.1:8888",
        "http://192.168.1.3",
        "file:///etc/passwd",
        "/relative/path",
    ],
)
def test_browser_extract_rejects_local_and_non_http_urls(url) -> None:
    with pytest.raises(BrowserToolError):
        _validate_public_url(url)
