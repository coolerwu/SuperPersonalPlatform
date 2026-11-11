from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services import sdk_bridge
from app.services.claude_env import claude_paths
from app.services.claude_settings import config_lock
from app.services.subprocess_utils import kill_process_group

OFFICIAL_REPO = "anthropics/claude-plugins-official"
# Passing the HTTPS URL avoids the CLI's SSH clone path (git@github.com),
# which needs keys and is much slower on typical self-hosted links.
OFFICIAL_SOURCE_URL = "https://github.com/anthropics/claude-plugins-official.git"
LOCAL_TIMEOUT_SECONDS = 60.0
NETWORK_TIMEOUT_SECONDS = 660.0
LOCK_WAIT_SECONDS = 30.0
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 800
STDERR_TAIL_CHARS = 4000
_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLUGIN_ID_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,127})@"
    r"(?P<marketplace>[A-Za-z0-9][A-Za-z0-9._-]{0,127})$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class PluginError(Exception):
    """Base class for plugin marketplace failures."""


class PluginUnavailableError(PluginError):
    """The Claude Code CLI binary could not be found."""


class PluginValidationError(PluginError):
    """The requested plugin operation is malformed or not allowed."""


class PluginNotFoundError(PluginError):
    """The requested marketplace or plugin is not configured."""


class PluginConflictError(PluginError):
    """The operation conflicts with current marketplace/plugin state."""


class PluginBusyError(PluginError):
    """Another plugin operation holds the CLI lock."""


class PluginCommandError(PluginError):
    """The Claude Code CLI exited non-zero or timed out."""


@dataclass(frozen=True)
class MarketplaceInfo:
    name: str
    repo: str
    install_location: str


@dataclass(frozen=True)
class InstalledPluginInfo:
    id: str
    name: str
    marketplace: str
    version: str
    enabled: bool
    scope: str
    installed_at: str
    last_updated: str


@dataclass(frozen=True)
class AvailablePluginInfo:
    name: str
    description: str
    author: str
    category: str
    homepage: str


@dataclass(frozen=True)
class PluginCatalog:
    cli_available: bool
    marketplaces: tuple[MarketplaceInfo, ...] = ()
    installed: tuple[InstalledPluginInfo, ...] = ()
    available: tuple[AvailablePluginInfo, ...] = ()
    errors: tuple[str, ...] = ()


# CLI mutations rewrite ``claude/settings.json`` and ``installed_plugins.json``;
# serialize every invocation so two requests cannot interleave. The retention
# sync writes settings.json too, so it shares the same lock.
def _lock() -> asyncio.Lock:
    return config_lock()


@asynccontextmanager
async def _serialized() -> Any:
    lock = _lock()
    try:
        async with asyncio.timeout(LOCK_WAIT_SECONDS):
            await lock.acquire()
    except TimeoutError:
        raise PluginBusyError("另一个插件操作正在进行，请稍后再试") from None
    try:
        yield
    finally:
        lock.release()


def _revision_path(home: Path | None) -> Path:
    return claude_paths(home)["config"] / ".plugins-revision"


def _touch_revision(home: Path | None) -> None:
    """Mark loaded plugin content as changed for the runner's fingerprint."""
    path = _revision_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{time.time_ns()}-{uuid.uuid4().hex}\n", encoding="utf-8"
        )
        os.chmod(path, 0o600)
    except OSError:
        # The CLI-written state files still cover most mutations; a failed
        # marker must not turn a successful install into an error.
        pass


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _load_json(stdout: str) -> Any:
    try:
        return json.loads(stdout)
    except ValueError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = stdout.find(opener)
        end = stdout.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stdout[start : end + 1])
            except ValueError:
                break
    raise PluginCommandError("无法解析 Claude Code CLI 的输出")


async def run_cli(
    args: list[str], *, timeout: float, home: Path | None = None
) -> str:
    """Run the bundled Claude Code CLI against the platform config dir."""
    cli = sdk_bridge.cli_path()
    if not cli:
        raise PluginUnavailableError("未找到 Claude Code CLI")
    paths = claude_paths(home)
    config = paths["config"]
    config.mkdir(parents=True, exist_ok=True)
    paths["tmp"].mkdir(parents=True, exist_ok=True)
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG")
        if key in os.environ
    }
    environment.update(
        {
            "CLAUDE_CONFIG_DIR": str(config),
            "TMPDIR": str(paths["tmp"]),
            "GIT_TERMINAL_PROMPT": "0",
            "NO_COLOR": "1",
            "TERM": "dumb",
            # The official marketplace clone can exceed the CLI's 120s
            # default on slow GitHub links; keep the outer timeout above it.
            "CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS": "600000",
        }
    )
    for key in _PROXY_KEYS:
        if key in os.environ:
            environment[key] = os.environ[key]
    process = await asyncio.create_subprocess_exec(
        cli,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config),
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        kill_process_group(process)
        await process.wait()
        raise PluginCommandError(
            f"claude {' '.join(args[:2])} 超时（{int(timeout)} 秒）"
        ) from None
    except asyncio.CancelledError:
        kill_process_group(process)
        await process.wait()
        raise
    if process.returncode != 0:
        detail = _strip_ansi(
            stderr.decode("utf-8", errors="replace").strip()
        )[-STDERR_TAIL_CHARS:]
        fallback = _strip_ansi(
            stdout.decode("utf-8", errors="replace").strip()
        )[-STDERR_TAIL_CHARS:]
        raise PluginCommandError(
            f"claude {' '.join(args[:2])} 失败（code {process.returncode}）："
            f"{detail or fallback or '没有错误输出'}"
        )
    return stdout.decode("utf-8", errors="replace")


def _normalize_git_url(url: str) -> str:
    """Turn a GitHub clone URL into ``owner/repo`` for official matching."""
    value = url.strip()
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.removesuffix(".git").strip("/")
    return value


def _parse_marketplaces(stdout: str) -> list[MarketplaceInfo]:
    data = _load_json(stdout)
    if not isinstance(data, list):
        raise PluginCommandError("集市列表格式无法解析")
    marketplaces: list[MarketplaceInfo] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        repo = _text(entry.get("repo"))
        if not repo:
            repo = _normalize_git_url(_text(entry.get("url")))
        location = _text(entry.get("installLocation"))
        if name and repo:
            marketplaces.append(
                MarketplaceInfo(
                    name=name, repo=repo, install_location=location
                )
            )
    return marketplaces


def _parse_installed(stdout: str) -> list[InstalledPluginInfo]:
    data = _load_json(stdout)
    if not isinstance(data, list):
        raise PluginCommandError("插件列表格式无法解析")
    installed: list[InstalledPluginInfo] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        plugin_id = _text(entry.get("id"))
        if not plugin_id or "@" not in plugin_id:
            continue
        name, marketplace = plugin_id.rsplit("@", 1)
        installed.append(
            InstalledPluginInfo(
                id=plugin_id,
                name=name,
                marketplace=marketplace,
                version=_text(entry.get("version")),
                enabled=entry.get("enabled") is not False,
                scope=_text(entry.get("scope")),
                installed_at=_text(entry.get("installedAt")),
                last_updated=_text(entry.get("lastUpdated")),
            )
        )
    return installed


def _parse_available(manifest_path: Path) -> list[AvailablePluginInfo]:
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise PluginCommandError("集市清单过大，无法读取")
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginCommandError(f"集市清单不可读：{exc}") from exc
    data = _load_json(raw)
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        raise PluginCommandError("集市清单格式无法解析")
    available: list[AvailablePluginInfo] = []
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if not name:
            continue
        author = entry.get("author")
        author_name = _text(author.get("name")) if isinstance(author, dict) else ""
        available.append(
            AvailablePluginInfo(
                name=name,
                description=_text(entry.get("description"))[
                    :MAX_DESCRIPTION_CHARS
                ],
                author=author_name,
                category=_text(entry.get("category")),
                homepage=_text(entry.get("homepage")),
            )
        )
    return available


def _official_marketplace(
    marketplaces: tuple[MarketplaceInfo, ...],
) -> MarketplaceInfo | None:
    return next(
        (item for item in marketplaces if item.repo == OFFICIAL_REPO), None
    )


def _manifest_path(
    official: MarketplaceInfo, home: Path | None
) -> Path:
    if official.install_location:
        return Path(official.install_location) / ".claude-plugin" / "marketplace.json"
    config = claude_paths(home)["config"]
    return (
        config
        / "plugins"
        / "marketplaces"
        / official.name
        / ".claude-plugin"
        / "marketplace.json"
    )


async def _catalog_locked(home: Path | None) -> PluginCatalog:
    if not sdk_bridge.cli_path():
        return PluginCatalog(
            cli_available=False,
            errors=("未找到 Claude Code CLI，无法管理插件集市。",),
        )
    errors: list[str] = []
    marketplaces: tuple[MarketplaceInfo, ...] = ()
    installed: tuple[InstalledPluginInfo, ...] = ()
    try:
        stdout = await run_cli(
            ["plugin", "marketplace", "list", "--json"],
            timeout=LOCAL_TIMEOUT_SECONDS,
            home=home,
        )
        marketplaces = tuple(_parse_marketplaces(stdout))
    except PluginError as exc:
        errors.append(f"读取集市失败：{exc}")
    try:
        stdout = await run_cli(
            ["plugin", "list", "--json"],
            timeout=LOCAL_TIMEOUT_SECONDS,
            home=home,
        )
        installed = tuple(_parse_installed(stdout))
    except PluginError as exc:
        errors.append(f"读取已安装插件失败：{exc}")

    official = _official_marketplace(marketplaces)
    for item in marketplaces:
        if item.repo != OFFICIAL_REPO:
            errors.append(f"已忽略非官方集市：{item.name}（{item.repo}）")

    available: tuple[AvailablePluginInfo, ...] = ()
    if official is not None:
        try:
            installed_ids = {item.id for item in installed}
            seen: set[str] = set()
            entries: list[AvailablePluginInfo] = []
            for plugin in _parse_available(_manifest_path(official, home)):
                if plugin.name in seen:
                    continue
                if f"{plugin.name}@{official.name}" in installed_ids:
                    continue
                seen.add(plugin.name)
                entries.append(plugin)
            available = tuple(entries)
        except PluginError as exc:
            errors.append(f"读取可安装列表失败：{exc}")

    return PluginCatalog(
        cli_available=True,
        marketplaces=(official,) if official is not None else (),
        installed=installed,
        available=available,
        errors=tuple(errors),
    )


async def catalog(home: Path | None = None) -> PluginCatalog:
    async with _serialized():
        return await _catalog_locked(home)


def _validate_plugin_name(name: str) -> str:
    value = name.strip()
    if not _NAME_RE.fullmatch(value):
        raise PluginValidationError("插件名称不合法")
    return value


def _parse_plugin_id(plugin_id: str) -> tuple[str, str]:
    match = _PLUGIN_ID_RE.fullmatch(plugin_id.strip())
    if match is None:
        raise PluginValidationError("插件 ID 不合法")
    return match.group("name"), match.group("marketplace")


def _require_installed(
    catalog_value: PluginCatalog, plugin_id: str
) -> InstalledPluginInfo:
    for item in catalog_value.installed:
        if item.id == plugin_id:
            return item
    raise PluginNotFoundError("插件未安装")


async def add_official_marketplace(
    home: Path | None = None,
) -> PluginCatalog:
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        if _official_marketplace(current.marketplaces) is not None:
            raise PluginConflictError("官方集市已添加")
        await run_cli(
            [
                "plugin",
                "marketplace",
                "add",
                OFFICIAL_SOURCE_URL,
                "--scope",
                "user",
            ],
            timeout=NETWORK_TIMEOUT_SECONDS,
            home=home,
        )
        return await _catalog_locked(home)


async def update_official_marketplace(
    home: Path | None = None,
) -> PluginCatalog:
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        official = _official_marketplace(current.marketplaces)
        if official is None:
            raise PluginNotFoundError("还没有添加官方集市")
        await run_cli(
            ["plugin", "marketplace", "update", official.name],
            timeout=NETWORK_TIMEOUT_SECONDS,
            home=home,
        )
        return await _catalog_locked(home)


async def remove_official_marketplace(
    home: Path | None = None,
) -> PluginCatalog:
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        official = _official_marketplace(current.marketplaces)
        if official is None:
            raise PluginNotFoundError("还没有添加官方集市")
        if any(
            error.startswith("读取已安装插件失败")
            for error in current.errors
        ):
            raise PluginCommandError("无法确认已安装插件，暂不能移除集市")
        if any(item.marketplace == official.name for item in current.installed):
            raise PluginConflictError("请先卸载该集市下的插件")
        await run_cli(
            ["plugin", "marketplace", "remove", official.name],
            timeout=LOCAL_TIMEOUT_SECONDS,
            home=home,
        )
        return await _catalog_locked(home)


async def install_plugin(
    name: str, home: Path | None = None
) -> PluginCatalog:
    plugin_name = _validate_plugin_name(name)
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        official = _official_marketplace(current.marketplaces)
        if official is None:
            raise PluginNotFoundError("请先添加官方集市")
        plugin_id = f"{plugin_name}@{official.name}"
        if any(item.id == plugin_id for item in current.installed):
            raise PluginConflictError("插件已安装")
        if plugin_name not in {item.name for item in current.available}:
            raise PluginNotFoundError("官方集市里没有这个插件，请先更新集市")
        await run_cli(
            ["plugin", "install", plugin_id, "--scope", "user"],
            timeout=NETWORK_TIMEOUT_SECONDS,
            home=home,
        )
        _touch_revision(home)
        return await _catalog_locked(home)


async def set_plugin_enabled(
    plugin_id: str, enabled: bool, home: Path | None = None
) -> PluginCatalog:
    _parse_plugin_id(plugin_id)
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        installed = _require_installed(current, plugin_id)
        if installed.enabled == enabled:
            return current
        await run_cli(
            [
                "plugin",
                "enable" if enabled else "disable",
                plugin_id,
                "--scope",
                "user",
            ],
            timeout=LOCAL_TIMEOUT_SECONDS,
            home=home,
        )
        _touch_revision(home)
        return await _catalog_locked(home)


async def update_plugin(
    plugin_id: str, home: Path | None = None
) -> PluginCatalog:
    _parse_plugin_id(plugin_id)
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        _require_installed(current, plugin_id)
        await run_cli(
            ["plugin", "update", plugin_id, "--scope", "user"],
            timeout=NETWORK_TIMEOUT_SECONDS,
            home=home,
        )
        _touch_revision(home)
        return await _catalog_locked(home)


async def uninstall_plugin(
    plugin_id: str, home: Path | None = None
) -> PluginCatalog:
    _parse_plugin_id(plugin_id)
    async with _serialized():
        current = await _catalog_locked(home)
        if not current.cli_available:
            raise PluginUnavailableError("未找到 Claude Code CLI")
        _require_installed(current, plugin_id)
        await run_cli(
            ["plugin", "uninstall", plugin_id, "--scope", "user"],
            timeout=LOCAL_TIMEOUT_SECONDS,
            home=home,
        )
        _touch_revision(home)
        return await _catalog_locked(home)
