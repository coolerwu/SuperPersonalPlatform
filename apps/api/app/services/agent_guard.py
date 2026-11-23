"""Per-Agent tool gating for restricted agents.

A restricted Agent (``agents[].access == "workspace"``) runs with
``permission_mode="dontAsk"``: tools not listed in ``allowed_tools`` are
refused without prompting. The whitelist below admits the normal
conversation tools, but file tools are further gated by a ``PreToolUse``
hook that resolves every path and rejects anything outside the Agent's
workspace. The hook never grants permission (it returns an empty decision
for calls it has no opinion on), so the ``dontAsk`` whitelist stays the
only allow path.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from app.services.claude_env import claude_paths

logger = logging.getLogger(__name__)

HookCallback = Callable[[Any, "str | None", Any], Awaitable[dict[str, Any]]]

# Tool name -> input fields that carry filesystem paths. Missing fields are
# treated as "no path to check"; malformed calls are left for the tool itself.
FILE_TOOL_PATH_FIELDS: dict[str, tuple[str, ...]] = {
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Glob": ("path", "pattern"),
    "Grep": ("path",),
}

# Tools a restricted Agent may call. File tools are safe only because the
# hook re-checks every path; everything not listed here is denied by dontAsk.
RESTRICTED_BASE_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Bash",
    "Task",
    "TodoWrite",
    "Skill",
)

_ABSOLUTE_PREFIXES = ("/", "~")
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def build_restricted_allowed_tools(
    *, maps_enabled: bool, plugin_servers: Iterable[str] = ()
) -> list[str]:
    """Allowed tools for a restricted Agent, including MCP entries."""
    names = list(RESTRICTED_BASE_TOOLS)
    for server in _server_allow_entries(plugin_servers):
        if server not in names:
            names.append(server)
    if maps_enabled:
        names.append("mcp__amap")
    # The app lifespan registers the browser MCP server; restricted Agents
    # need the server-level entry or dontAsk denies every browser tool.
    if "mcp__browser" not in names:
        names.append("mcp__browser")
    from app.services import platform_mcp

    for tool in platform_mcp.allowed_tool_names():
        if tool not in names:
            names.append(tool)
    return names


def _server_allow_entries(plugin_servers: Iterable[str]) -> list[str]:
    entries: list[str] = []
    for server in plugin_servers:
        if not server:
            continue
        entry = f"mcp__{server}"
        if entry not in entries:
            entries.append(entry)
    return entries


def installed_mcp_server_names(home: Path | None = None) -> list[str]:
    """MCP server names declared by installed plugins.

    Reads ``claude/plugins/installed_plugins.json`` and each plugin's
    ``.mcp.json``. Unreadable or malformed data is skipped; a plugin whose
    servers cannot be discovered simply stays denied by ``dontAsk``.
    """
    config = claude_paths(home)["config"]
    meta_path = config / "plugins" / "installed_plugins.json"
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(meta, dict):
        return []
    plugins = meta.get("plugins")
    if not isinstance(plugins, dict):
        return []
    servers: set[str] = set()
    for entries in plugins.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            install = entry.get("installPath")
            if not isinstance(install, str) or not install:
                continue
            manifest = Path(install) / ".mcp.json"
            try:
                data = json.loads(manifest.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                servers.update(name for name in data if isinstance(name, str))
    return sorted(servers)


def workspace_guard(workspace: Path) -> HookCallback:
    """Build the PreToolUse hook that confines file tools to ``workspace``."""
    root = workspace.resolve()

    async def guard(
        input: Any, tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        if not isinstance(input, dict):
            return _deny("工具调用参数无法解析，已拒绝")
        tool = str(input.get("tool_name") or "")
        tool_input = input.get("tool_input")
        if tool == "WebFetch":
            return _check_webfetch(tool_input)
        fields = FILE_TOOL_PATH_FIELDS.get(tool)
        if fields is None:
            return {}
        return _check_paths(root, tool_input, fields)

    return guard


def _check_paths(
    root: Path, tool_input: Any, fields: tuple[str, ...]
) -> dict[str, Any]:
    if not isinstance(tool_input, dict):
        return _deny("文件工具参数无法解析，已拒绝")
    for field in fields:
        raw = tool_input.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if field == "pattern":
            if _pattern_escapes(raw):
                return _deny(
                    "该路径不在当前 Agent 的工作区内，已拒绝。工作区："
                    f"{root}"
                )
            continue
        if not _inside(root, raw):
            return _deny(
                "该路径不在当前 Agent 的工作区内，已拒绝。工作区："
                f"{root}"
            )
    return {}


def _pattern_escapes(value: str) -> bool:
    """True when a glob/regex pattern is absolute or climbs upwards."""
    text = value.strip()
    if text.startswith(_ABSOLUTE_PREFIXES):
        return True
    return ".." in Path(text).parts


def _inside(root: Path, raw: str) -> bool:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def _check_webfetch(tool_input: Any) -> dict[str, Any]:
    if not isinstance(tool_input, dict):
        return _deny("WebFetch 参数无法解析，已拒绝")
    url = tool_input.get("url")
    if not isinstance(url, str) or not url.strip():
        return {}
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return _deny("WebFetch 只允许 http/https 地址")
    host = (parts.hostname or "").strip().lower()
    if not host:
        return _deny("WebFetch 地址缺少主机名，已拒绝")
    if host == "localhost" or host.endswith(".localhost"):
        return _deny("WebFetch 不允许访问本机地址")
    if _is_private_ip(host):
        return _deny("WebFetch 不允许访问本机或内网地址")
    return {}


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _PRIVATE_NETWORKS)


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
