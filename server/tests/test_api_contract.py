"""Guard the API contract between docs/project-architecture.md and the app.

`docs/project-architecture.md` is part of the project contract: it enumerates the
HTTP surface the platform promises to expose. These tests keep the document and
the FastAPI application honest in both directions, so a new endpoint cannot be
merged without a documented path and a documented path cannot survive a removal.
"""

import re
from pathlib import Path

from server.infrastructure.config import load_settings
from server.infrastructure.fastapi_app import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE_DOC = PROJECT_ROOT / "docs" / "project-architecture.md"

API_PATH_PATTERN = re.compile(r"/api/[A-Za-z0-9_{}*/\-]*")


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
"""


def _implemented_paths(tmp_path: Path) -> set[str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text(CONFIG, encoding="utf-8")
    app = create_app(load_settings(workspace / "config.yaml"), workspace=workspace)
    return set(app.openapi()["paths"])


def _documented_surface() -> tuple[set[str], set[str]]:
    """Return (exact documented paths, documented route families)."""
    text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    paths: set[str] = set()
    families: set[str] = set()
    for raw in API_PATH_PATTERN.findall(text):
        if "*" in raw:
            family = raw.split("*", 1)[0].rstrip("/")
            if family:
                families.add(family)
            continue
        candidate = raw.rstrip("/")
        if candidate:
            paths.add(candidate)
    return paths, families


def _segment_regex(route_template: str) -> re.Pattern[str]:
    segments = [segment for segment in route_template.split("/") if segment]
    parts = ["^"]
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(r"/[^/]+")
        else:
            parts.append("/" + re.escape(segment))
    parts.append("/?$")
    return re.compile("".join(parts))


def _route_matches(route_template: str, candidate: str) -> bool:
    return bool(_segment_regex(route_template).match(candidate))


def test_every_documented_api_path_is_implemented(tmp_path: Path) -> None:
    implemented = _implemented_paths(tmp_path)
    documented, families = _documented_surface()

    missing = sorted(
        path
        for path in documented
        if not any(_route_matches(route, path) for route in implemented)
    )

    assert missing == [], f"documented but not implemented: {missing}"

    empty_families = sorted(
        family
        for family in families
        if not any(route.startswith(family + "/") for route in implemented)
    )

    assert empty_families == [], (
        "documented route families without any implemented endpoint: "
        f"{empty_families}"
    )


def test_every_implemented_api_path_is_documented(tmp_path: Path) -> None:
    implemented = _implemented_paths(tmp_path)
    documented, families = _documented_surface()

    undocumented = sorted(
        route
        for route in implemented
        if not any(_route_matches(route, path) for path in documented)
        and not any(route.startswith(family + "/") for family in families)
    )

    assert undocumented == [], (
        "implemented but not documented in docs/project-architecture.md: "
        f"{undocumented}"
    )
