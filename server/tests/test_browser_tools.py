import pytest

from server.domain.tooling import get_tool_definition
from server.infrastructure.browser_tools import BrowserToolError, _validate_public_url
from server.infrastructure.tool_runtime import build_platform_tools


def test_browser_extract_is_a_platform_tool(tmp_path) -> None:
    definition = get_tool_definition("browser_extract")
    tools = build_platform_tools(("browser_extract",), context_workspace=tmp_path / "context")

    assert definition.name == "Browser Extract"
    assert tools[0].name == "browser_extract"


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
