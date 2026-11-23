"""Single-turn executor backed by the Claude Agent SDK.

One runner instance exists per process. It keeps a per-session
``ClaudeSDKClient`` cache so a session's next turn resumes the same SDK
conversation; the turn lease row marks which session currently has a turn
attached, and the rich ``turn_end`` event (status/error/usage) is published
here rather than by ``TurnStream.close``. A client is evicted after timeouts,
failures and streams that end without this turn's own result, so a poisoned
stream cannot leak into the next turn. Run persistence, queueing and
cancellation policy stay outside this module.

Each cached client keeps a CLI subprocess alive, so the cache is bounded by an
idle TTL and an LRU size cap. A low-frequency sweep evicts idle clients, turn
start/end touches the entry, and a cached entry whose Agent configuration
(instructions/provider) changed is rebuilt before the next turn. Eviction
disconnects with a timeout and never blocks a concurrency permit, and shutdown
uses a per-client budget instead of dropping references to live subprocesses.

The runner also owns a turn's channel delivery obligation: the pending row is
written in the same transaction as the lease, filled with the final answer at
teardown (or discarded when no reply is owed), and handed to
``deliver_callback`` when one is configured.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from app.config import settings as app_settings
from app.db import SessionLocal
from app.services import (
    agent_guard,
    browser_mcp,
    channel_delivery,
    platform_mcp,
    sdk_bridge,
    turn_lease,
)
from app.services.claude_env import build_claude_env, claude_paths, workspace_for
from app.services.identifiers import valid_agent_id
from app.services.sdk_events import (
    StreamMapper,
    TurnOutcome,
    origin_kind,
    outcome_from_result,
    subagent_message_events,
    task_event,
)
from app.services.settings_store import SettingsStore
from app.services.skill_tools import SkillToolContext
from app.services.tool_artifacts import artifact_from_tool
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)

# The SDK's transport close closes stdin, waits, sends SIGTERM, waits, then
# SIGKILL, with roughly a 20 s worst-case bound. Before that, Query.close()
# waits up to 5 s for each in-process SDK MCP bridge to wind down; the platform
# always has one (``platform_tools``). An asyncio.timeout at that boundary
# raw-cancels the SDK's shielded cleanup and skips the escalation, so leave
# 20 + 5 + 5 s of margin before treating a close as an orphan.
_DISCONNECT_TIMEOUT_SECONDS = 30
# Total time ``close_all`` may spend disconnecting cached clients. Clients are
# disconnected concurrently, so the budget covers one SDK close, not the sum.
# The deploy unit allows 60 s for the whole shutdown; ``SessionRegistry.close``
# may take up to 10 s, leaving room for the other runtimes.
_SHUTDOWN_BUDGET_SECONDS = 35
# A connect that wedges would otherwise hold a concurrency permit forever.
# The SDK's own initialize timeout is max(CLAUDE_CODE_STREAM_CLOSE_TIMEOUT, 60s),
# so leave it room to report its error before this outer timeout fires.
_CONNECT_TIMEOUT_SECONDS = 75
_INTERRUPT_LOCK_TIMEOUT_SECONDS = 5
# Big tool results and file reads must not overflow the SDK's default 1 MB
# CLI frame buffer and fail the turn.
_MAX_BUFFER_SIZE = 16 * 1024 * 1024
# Clients whose disconnect timed out are kept referenced; bound that list so a
# long-running process cannot grow it without limit.
_MAX_ORPHANED_CLIENTS = 32
_ORPHAN_RETRY_BUDGET_SECONDS = 10

# Channel users get a reply even when the turn produced no text at all.
_EMPTY_REPLY_TEXT = "（无回复）"
_FAILED_DELIVERY_TEXT = "（任务执行失败，请稍后重试。）"

ClientKey = tuple[str, str]


@dataclass
class _CachedClient:
    client: Any
    agent_id: str
    fingerprint: str
    last_used: float
    # Background-task reader that owns ``client.receive_messages()`` between
    # turns; the turn loop owns the stream while a turn is running.
    tail_reader: asyncio.Task[None] | None = None


def _memory_stamp(home: Path | None) -> str:
    """Identity of the shared user profile loaded from ``claude/CLAUDE.md``."""
    path = claude_paths(home)["config"] / "CLAUDE.md"
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _agent_memory_stamp(agent_id: str, home: Path | None) -> str:
    """Identity of the project memory in ``workspaces/<agent>/CLAUDE.md``."""
    if not valid_agent_id(agent_id):
        return ""
    path = workspace_for(agent_id, home) / "CLAUDE.md"
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _plugin_stamp(home: Path | None) -> str:
    """Identity of the plugin state baked into a connected SDK client.

    ``settings.json`` changes when plugins are enabled/disabled and
    ``installed_plugins.json`` when they are installed/updated/removed. The
    revision marker covers updates whose CLI writes do not touch either file.
    """
    config = claude_paths(home)["config"]
    parts: list[str] = []
    for path in (
        config / "settings.json",
        config / "plugins" / "installed_plugins.json",
        config / ".plugins-revision",
    ):
        try:
            stat = path.stat()
        except OSError:
            parts.append("")
            continue
        parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


_AMAP_MCP_URL = "https://mcp.amap.com/mcp"
_AMAP_MCP_PATH = _AMAP_MCP_URL.removeprefix("https://")
# Match the URL with or without a scheme, case-insensitively, and stop at a
# quote, whitespace, or the next query parameter so JSON-embedded URLs keep
# their surrounding punctuation.
_AMAP_KEY_RE = re.compile(
    r"((?:https?://)?)" + re.escape(_AMAP_MCP_PATH) + r"\?key=[^\"\s&]+",
    re.IGNORECASE,
)


def _redact_cli_line(line: str) -> str:
    return _AMAP_KEY_RE.sub(r"\1" + _AMAP_MCP_PATH + "?key=***", line)


_SIGNAL_EXIT_CODES = {"143", "137", "-15", "-9"}


def _is_signal_exit(exc: BaseException) -> bool:
    """Whether the SDK CLI was killed by a signal.

    systemd sends SIGTERM to the whole unit cgroup on restart (until
    KillMode=mixed is deployed), so the CLI can exit with 143 before the app
    cancels the turn. Treat that as an interruption: keep the lease and let
    startup recovery resume it.
    """
    if type(exc).__name__ != "ProcessError":
        return False
    match = re.search(r"exit code (-?\d+)", str(exc))
    return bool(match and match.group(1) in _SIGNAL_EXIT_CODES)


def _maps_stamp(maps: Any) -> str:
    return json.dumps(
        {
            "enabled": bool(maps.enabled),
            "provider": maps.provider,
            "api_key": maps.api_key,
        },
        sort_keys=True,
    )


def _config_fingerprint(
    agent: Any,
    memory_stamp: str = "",
    plugin_stamp: str = "",
    agent_memory_stamp: str = "",
    maps_stamp: str = "",
) -> str:
    """Hash the settings that are baked into a connected SDK client."""
    provider = agent.provider
    payload = json.dumps(
        {
            "instructions": agent.instructions or "",
            "access": getattr(agent, "access", "full"),
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": provider.api_key,
            "memory": memory_stamp,
            "agent_memory": agent_memory_stamp,
            "plugins": plugin_stamp,
            "maps": maps_stamp,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class DeliveryRequest:
    """Channel obligation for this turn.

    ``delivery_id`` is the ``queued`` row channel intake created before the
    turn was submitted; the runner promotes it to ``pending`` when the turn
    actually starts, in the same transaction as the lease. A request without
    an id (a legacy caller or a cron push) makes the runner create the row
    itself at turn start. The row is filled with the final answer and handed
    to ``deliver_callback`` at teardown.
    """

    channel_id: str
    external_id: str
    delivery_id: int | None = None


@dataclass
class Turn:
    session_id: str
    agent_id: str
    prompt: str
    source: str = "manual"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    delivery: DeliveryRequest | None = None


def _delivery_content(
    outcome: TurnOutcome, *, interrupted: bool, result_received: bool
) -> str | None:
    """Return the channel reply body, or ``None`` to drop the obligation.

    Completed turns always answer (a placeholder when the model produced no
    text); failed turns answer with their partial text or a fixed error;
    cancelled or interrupted-without-result turns owe nothing.
    """
    if outcome.status == "cancelled":
        return None
    if interrupted and not result_received:
        return None
    if outcome.status == "completed":
        return outcome.output if outcome.output.strip() else _EMPTY_REPLY_TEXT
    return outcome.output if outcome.output.strip() else _FAILED_DELIVERY_TEXT


async def _best_effort(action: Callable[[], Awaitable[Any]], label: str) -> Any:
    """Run one delivery teardown step without breaking the finally block."""
    try:
        return await action()
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            logger.warning("%s cancelled", label)
        else:
            logger.warning("%s failed", label, exc_info=True)
        return None


def transcript_dir(agent_id: str, *, home: Path | None = None) -> Path:
    return claude_paths(home)["config"] / "projects" / agent_id


def transcript_path(session_id: str, agent_id: str, *, home: Path | None = None) -> Path:
    return transcript_dir(agent_id, home=home) / f"{session_id}.jsonl"


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in result:
            result.append(text)
    return result


def _rows(value: Any, keys: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append({key: str(item.get(key) or "") for key in keys})
    return rows


class ClaudeTurnRunner:
    def __init__(
        self,
        store: SettingsStore,
        *,
        home: Path | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        deliver_callback: Callable[[int], Any] | None = None,
        client_ttl_seconds: float | None = None,
        max_clients: int | None = None,
        sweep_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
        session_tasks: Any | None = None,
        browser_service: Any | None = None,
    ) -> None:
        self._store = store
        self._home = home
        # Keyed by (agent_id, session_id): a session id reused across Agents
        # must never reuse another Agent's workspace/instructions/credentials.
        self._clients: dict[ClientKey, _CachedClient] = {}
        self._factory = client_factory
        self._deliver_callback = deliver_callback
        # Session-level background task state: task events are published here
        # and active tasks pin a cached client past its idle TTL.
        self._session_tasks = session_tasks
        # Shared browser owned by the app lifespan; when absent the browser
        # MCP server is not registered at all.
        self._browser_service = browser_service
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: set[ClientKey] = set()
        # Turn id currently attached to each cached client key. /stop uses it
        # so a late interrupt cannot hit the session's next turn.
        self._active_turns: dict[ClientKey, str] = {}
        # Keys whose prompt has been written to the CLI. An interrupt before
        # this point must wait for the runner to send it after query().
        self._query_sent: set[ClientKey] = set()
        self._cancel_requested: set[ClientKey] = set()
        # Turns that ended in a state that must not be resumed reuse; the
        # registry drains these after releasing its concurrency permit.
        self._evict_pending: set[ClientKey] = set()
        self._client_ttl = (
            app_settings.claude_client_ttl_seconds
            if client_ttl_seconds is None
            else client_ttl_seconds
        )
        self._max_clients = (
            app_settings.claude_client_max_clients
            if max_clients is None
            else max_clients
        )
        self._sweep_seconds = (
            app_settings.claude_client_sweep_seconds
            if sweep_seconds is None
            else sweep_seconds
        )
        self._connect_timeout = (
            app_settings.claude_connect_timeout_seconds
            if connect_timeout_seconds is None
            else connect_timeout_seconds
        )
        self._sweeper: asyncio.Task[None] | None = None
        # Last init frame reported by the CLI, per Agent. Claude Code owns the
        # tool, MCP, plugin, skill and slash-command lists; the web UI mirrors
        # them instead of keeping its own copies.
        self._init_frames: dict[str, dict[str, Any]] = {}
        # Clients whose disconnect exceeded the shutdown budget. They must be
        # kept referenced so the CLI process is not silently orphaned; the
        # process dies with this one.
        self._orphaned_clients: list[Any] = []

    def _client_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def _capture_init(self, agent_id: str, message: Any) -> None:
        """Remember the runtime capabilities a CLI init frame reports."""
        data = getattr(message, "data", None)
        if not isinstance(data, dict) or getattr(message, "subtype", "") != "init":
            return
        terminal = data.get("terminal_slash_commands")
        blocked = (
            {
                str(item).strip().lstrip("/")
                for item in terminal
                if isinstance(item, str)
            }
            if isinstance(terminal, list)
            else set()
        )
        commands: list[str] = []
        raw_commands = data.get("slash_commands")
        for item in raw_commands if isinstance(raw_commands, list) else []:
            if not isinstance(item, str):
                continue
            name = item.strip().lstrip("/")
            if not name or name in blocked or name in commands:
                continue
            commands.append(name)
        self._init_frames[agent_id] = {
            "slash_commands": commands,
            "tools": _strings(data.get("tools")),
            "mcp_servers": _rows(
                data.get("mcp_servers"), ("name", "status", "source")
            ),
            "plugins": _rows(data.get("plugins"), ("name", "version")),
            "plugin_errors": _rows(
                data.get("plugin_errors"), ("plugin", "type", "message")
            ),
            "skills": _strings(data.get("skills")),
            "claude_code_version": str(data.get("claude_code_version") or ""),
            "model": str(data.get("model") or ""),
            "permission_mode": str(data.get("permissionMode") or ""),
        }

    def slash_commands(self, agent_id: str) -> list[str]:
        """Slash commands the CLI currently exposes for ``agent_id``."""
        frame = self._init_frames.get(agent_id) or {}
        return list(frame.get("slash_commands", ()))

    def runtime_snapshot(self, agent_id: str) -> dict[str, Any] | None:
        """Latest init-frame capabilities for ``agent_id``, if any."""
        frame = self._init_frames.get(agent_id)
        return copy.deepcopy(frame) if frame is not None else None

    def _keys_for_session(self, session_id: str) -> list[ClientKey]:
        keys = set(self._clients) | self._active | self._cancel_requested
        return [key for key in keys if key[1] == session_id]

    async def _client(self, turn: Turn, agent: Any, maps: Any) -> Any:
        lock = self._client_lock(turn.session_id)
        key = (agent.id, turn.session_id)
        memory_stamp = _memory_stamp(self._home)
        plugin_stamp = _plugin_stamp(self._home)
        agent_memory_stamp = _agent_memory_stamp(agent.id, self._home)
        maps_stamp = _maps_stamp(maps)
        async with lock:
            cached = self._clients.get(key)
            if cached is not None:
                fingerprint = _config_fingerprint(
                    agent,
                    memory_stamp,
                    plugin_stamp,
                    agent_memory_stamp,
                    maps_stamp,
                )
                if cached.fingerprint != fingerprint:
                    # The Agent or Provider changed while this session was
                    # busy; rebuild instead of running with stale settings.
                    self._clients.pop(key, None)
                    try:
                        await self._stop_tail_reader(cached)
                        await self._disconnect(cached.client, turn.session_id)
                    except asyncio.CancelledError:
                        # Keep the reference so shutdown can still close it.
                        self._retain_orphan(cached.client)
                        raise
                else:
                    self._touch(key)
                    return cached.client
            # Agent ids become the SDK project directory and a workspace path
            # component; refuse malformed settings before creating anything.
            if not valid_agent_id(agent.id):
                raise ValueError(f"invalid agent id: {agent.id!r}")
            options_class = sdk_bridge.options_class()
            exists = transcript_path(turn.session_id, agent.id, home=self._home).is_file()
            workspace = workspace_for(agent.id, self._home)
            workspace.mkdir(parents=True, exist_ok=True)
            if not (
                agent.provider.base_url
                and agent.provider.model
                and agent.provider.api_key
            ):
                raise RuntimeError("provider base_url/model/api_key is not configured")
            kwargs: dict[str, Any] = {
                "cwd": str(workspace),
                "env": build_claude_env(agent, home=self._home),
                "include_partial_messages": True,
                # Subagent text/thinking arrives as messages tagged with the
                # spawning tool_use id; without this only its tool calls are
                # forwarded and the chat cannot follow what it is doing.
                "forward_subagent_text": True,
                "setting_sources": ["user", "project"],
                "max_buffer_size": _MAX_BUFFER_SIZE,
                # Surface CLI stderr at debug level so a failed turn is
                # diagnosable without exposing it to the chat.
                "stderr": lambda line: logger.debug(
                    "cli[%s] %s", turn.session_id, _redact_cli_line(line)
                ),
            }
            tool_context = SkillToolContext(
                api_key=agent.provider.api_key,
                base_url=agent.provider.base_url,
                agent_id=agent.id,
                session_id=turn.session_id,
            )
            mcp_servers: dict[str, Any] = {
                platform_mcp.SERVER_NAME: platform_mcp.build_server(
                    tool_context
                )
            }
            if self._browser_service is not None:
                mcp_servers[browser_mcp.SERVER_NAME] = browser_mcp.build_server(
                    self._browser_service,
                    agent_id=agent.id,
                    session_id=turn.session_id,
                    workspace=workspace,
                )
            amap_key = (maps.api_key or "").strip()
            if maps.provider == "amap" and maps.enabled and amap_key:
                mcp_servers["amap"] = {
                    "type": "http",
                    "url": f"{_AMAP_MCP_URL}?key={quote(amap_key, safe='')}",
                }
            kwargs["mcp_servers"] = mcp_servers
            access = getattr(agent, "access", "full")
            if access == "workspace":
                maps_enabled = (
                    maps.provider == "amap" and bool(maps.enabled) and bool(amap_key)
                )
                kwargs["permission_mode"] = "dontAsk"
                kwargs["allowed_tools"] = agent_guard.build_restricted_allowed_tools(
                    maps_enabled=maps_enabled,
                    plugin_servers=agent_guard.installed_mcp_server_names(self._home),
                )
                matcher_class = sdk_bridge.hook_matcher_class()
                kwargs["hooks"] = {
                    "PreToolUse": [
                        matcher_class(
                            matcher=None,
                            hooks=[agent_guard.workspace_guard(workspace)],
                            on_failure="block",
                        )
                    ]
                }
            else:
                kwargs["permission_mode"] = app_settings.claude_permission_mode
                # Map MCP tools are available; bypassPermissions auto-approves them.
                allowed_tools = platform_mcp.allowed_tool_names()
                if self._browser_service is not None:
                    allowed_tools = allowed_tools + browser_mcp.allowed_tool_names()
                kwargs["allowed_tools"] = allowed_tools
            # Keep Claude Code's default system prompt and append the Agent's
            # own instructions. The preset without ``append`` adds no CLI flag,
            # so an empty instruction set leaves the default prompt untouched.
            instructions = (agent.instructions or "").strip()
            system_prompt: dict[str, Any] = {
                "type": "preset",
                "preset": "claude_code",
            }
            if instructions:
                system_prompt["append"] = instructions
            kwargs["system_prompt"] = system_prompt
            if exists:
                kwargs["resume"] = turn.session_id
            else:
                kwargs["session_id"] = turn.session_id
            options = options_class(**kwargs)
            if self._factory is not None:
                client = self._factory(options)
            else:
                client = sdk_bridge.claude_client_class()(options)
            try:
                async with asyncio.timeout(self._connect_timeout):
                    await client.connect()
            except TimeoutError as exc:
                # A wedged connect must not keep a concurrency permit; drop
                # the half-connected client and report a normal turn failure.
                await self._disconnect(client, turn.session_id)
                raise RuntimeError("claude client connect timed out") from exc
            self._clients[key] = _CachedClient(
                client=client,
                agent_id=agent.id,
                fingerprint=_config_fingerprint(
                    agent,
                    memory_stamp,
                    plugin_stamp,
                    agent_memory_stamp,
                    maps_stamp,
                ),
                last_used=time.monotonic(),
            )
        self._ensure_sweeper()
        # Capacity is enforced outside the per-session lock so two concurrent
        # connects can never deadlock evicting each other.
        await self._enforce_capacity()
        return client

    # ------------------------------------------------------------ client cache

    def _touch(self, key: ClientKey) -> None:
        cached = self._clients.get(key)
        if cached is not None:
            cached.last_used = time.monotonic()

    def _ensure_sweeper(self) -> None:
        if self._sweeper is not None and not self._sweeper.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._sweeper = loop.create_task(
            self._sweep_loop(), name="claude-client-sweep"
        )

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(1.0, self._sweep_seconds))
                try:
                    await self.sweep_idle_clients()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("claude client sweep failed")
        except asyncio.CancelledError:
            raise

    def _idle_entries(self, *, now: float | None = None) -> list[tuple[ClientKey, _CachedClient]]:
        moment = time.monotonic() if now is None else now
        return [
            (key, cached)
            for key, cached in self._clients.items()
            if key not in self._active
            and moment - cached.last_used >= self._client_ttl
        ]

    async def sweep_idle_clients(self) -> int:
        """Evict clients whose session has been idle past the TTL.

        Entries are revalidated under the session lock: a turn may start (and
        mark itself active) while an earlier entry's disconnect is in flight,
        so a stale snapshot must never disconnect a live session.
        """
        expired = self._idle_entries()
        evicted = 0
        now = datetime.now(timezone.utc)
        for key, cached in expired:
            if self._session_tasks is not None:
                try:
                    deadline = await self._session_tasks.keep_alive_until(key[1])
                except Exception:
                    logger.exception(
                        "failed to read session task keep-alive session=%s",
                        key[1],
                    )
                    deadline = None
                if deadline is not None and deadline > now:
                    continue
            if await self._evict_key(key, expected=cached):
                evicted += 1
        await self._enforce_capacity()
        await self._retry_orphans()
        if evicted:
            logger.info("evicted %d idle SDK client(s)", evicted)
        return evicted

    async def _enforce_capacity(self) -> None:
        # Keep at least one client cached even if the cap is misconfigured to 0.
        limit = max(1, self._max_clients)
        overflow = len(self._clients) - limit
        if overflow <= 0:
            return
        idle = sorted(
            (
                (cached.last_used, key, cached)
                for key, cached in self._clients.items()
                if key not in self._active
            ),
            key=lambda item: item[0],
        )
        now = datetime.now(timezone.utc)
        skipped: list[tuple[float, ClientKey, _CachedClient]] = []
        for item in idle:
            if len(self._clients) <= limit:
                return
            _, key, cached = item
            if self._session_tasks is not None:
                try:
                    deadline = await self._session_tasks.keep_alive_until(key[1])
                except Exception:
                    logger.exception(
                        "failed to read session task keep-alive session=%s",
                        key[1],
                    )
                    deadline = None
                if deadline is not None and deadline > now:
                    skipped.append(item)
                    continue
            await self._evict_key(key, expected=cached)
        # Background tasks may pin clients past the normal cap; allow a small
        # grace, then evict the oldest pinned client and mark its tasks.
        for _, key, cached in skipped:
            if len(self._clients) <= limit + 2:
                return
            await self._evict_key(key, expected=cached)

    async def _stop_tail_reader(self, cached: _CachedClient) -> None:
        """Stop the between-turns task reader so the turn loop owns the stream.

        The await is part of the handoff: once it returns the cancelled reader
        can no longer publish, so eviction can safely mark its tasks interrupted
        and a new turn can read the same client.
        """
        task = cached.tail_reader
        if task is None:
            return
        cached.tail_reader = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                # This coroutine itself is being cancelled (shutdown, turn
                # timeout); never swallow the outer cancellation.
                raise
        except Exception:
            logger.exception("session task tail reader failed")

    def _start_tail_reader(self, key: ClientKey, cached: _CachedClient) -> None:
        if self._session_tasks is None or cached.tail_reader is not None:
            return
        cached.tail_reader = asyncio.create_task(
            self._drain_tail(key, cached),
            name=f"session-task-tail-{key[1]}",
        )

    async def _drain_tail(self, key: ClientKey, cached: _CachedClient) -> None:
        """Publish background task events that arrive after a turn's result.

        The CLI keeps emitting ``task_*`` frames while a background task runs;
        without a reader here they would sit in the SDK buffer until the next
        turn, which is exactly the window F26 must cover. Only task lifecycle
        events are published: injected turns are suppressed by the platform and
        must not leak into the chat.
        """
        try:
            async for message in cached.client.receive_messages():
                mapped = task_event(message)
                if mapped is None:
                    continue
                if self._session_tasks is not None:
                    try:
                        await self._session_tasks.publish(key[1], mapped)
                    except Exception:
                        logger.exception(
                            "failed to publish tail task event session=%s",
                            key[1],
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "session task tail reader ended session=%s", key[1], exc_info=True
            )

    async def run(
        self,
        turn: Turn,
        stream: TurnStream,
        *,
        defer_eviction: bool = False,
    ) -> TurnOutcome:
        key: ClientKey = (turn.agent_id, turn.session_id)
        # Mark active before any await so /stop can reach a turn that is still
        # loading settings or connecting its client.
        self._active.add(key)
        self._active_turns[key] = turn.id
        self._touch(key)
        mapper = StreamMapper()
        workspace = workspace_for(turn.agent_id, self._home)
        text_parts: list[str] = []
        outcome = TurnOutcome(status="failed", error="run ended without result")
        lease_added = False
        delivery_id: int | None = None
        result_received = False
        evict = False
        # Cancellation is shutdown, not failure: keep the lease so startup
        # recovery can resume the session after the restart.
        keep_lease = False
        try:
            async with asyncio.timeout(app_settings.run_timeout_seconds):
                settings = await self._store.get()
                agent = next(
                    (item for item in settings.agents if item.id == turn.agent_id),
                    None,
                )
                if agent is None:
                    outcome = TurnOutcome(status="failed", error="agent not found")
                    return outcome
                lease_added = True
                # Channel intake already created this obligation as ``queued``
                # before submitting the turn; adopt it now that the turn is
                # really starting.
                if turn.delivery is not None and turn.source != "resume":
                    delivery_id = turn.delivery.delivery_id
                # Lease and delivery obligation are written together so a
                # crash can never leave a turn marked running without the
                # reply it owes, or vice versa.
                async with SessionLocal() as db:
                    await turn_lease.add_in(db, turn.session_id)
                    if delivery_id is not None:
                        # Promote the pre-created row; if it is no longer
                        # queued the turn simply owes no reply.
                        if not await channel_delivery.mark_started_in(
                            db, delivery_id
                        ):
                            delivery_id = None
                    elif turn.delivery is not None and turn.source != "resume":
                        # Legacy or cron request without a pre-created row.
                        delivery_id = await channel_delivery.create_pending(
                            db,
                            channel_id=turn.delivery.channel_id,
                            external_id=turn.delivery.external_id,
                            session_id=turn.session_id,
                        )
                    # A resumed turn must attach to an obligation created before
                    # the crash, never create another one (that would double
                    # reply); the lookup below is its only source.
                    await db.commit()
                # A resumed turn reuses the empty obligation created by the
                # interrupted turn; a filled row already belongs to the sender
                # and must never be reloaded or duplicated.
                if delivery_id is None and turn.source == "resume":
                    existing = await channel_delivery.find_unfilled_for_session(
                        turn.session_id
                    )
                    if existing is not None:
                        delivery_id = existing.id
                # Abort before spawning/connecting a client if /stop arrived
                # while settings were loading.
                if key in self._cancel_requested:
                    self._cancel_requested.discard(key)
                    outcome = TurnOutcome(
                        status="cancelled", output="".join(text_parts)
                    )
                    return outcome
                client = await self._client(turn, agent, settings.maps)
                self._touch(key)
                # The previous turn's tail reader must yield the stream before
                # this turn reads it; two consumers would split the messages.
                cached = self._clients.get(key)
                if cached is not None:
                    await self._stop_tail_reader(cached)
                if turn.source == "resume":
                    prompt = (
                        "上次任务因服务重启被中断。请先检查当前状态，"
                        "继续完成原任务；已经完成的步骤不要重复执行。"
                    )
                else:
                    prompt = turn.prompt
                if key in self._cancel_requested:
                    self._cancel_requested.discard(key)
                    outcome = TurnOutcome(
                        status="cancelled", output="".join(text_parts)
                    )
                    return outcome
                await client.query(prompt)
                self._query_sent.add(key)
                if key in self._cancel_requested:
                    # /stop arrived while the prompt was being written; the
                    # interrupt sent then may have reached the CLI before the
                    # prompt, so send one now that the turn is in flight.
                    try:
                        await asyncio.wait_for(
                            client.interrupt(), timeout=5
                        )
                    except Exception:
                        # The turn is not actually cancelled; let it finish
                        # and deliver its reply instead of leaving the marker
                        # that suppresses channel delivery.
                        self._cancel_requested.discard(key)
                        logger.warning(
                            "interrupt after queued prompt failed session=%s",
                            turn.session_id,
                            exc_info=True,
                        )
                # Drain the full stream: receive_response() would stop at the
                # first ResultMessage, which may be an injected background
                # task notification rather than this turn's result.
                injected = False
                subagent_mappers: dict[str, StreamMapper] = {}
                subagent_streamed: set[str] = set()
                async for message in client.receive_messages():
                    mapped_task = task_event(message)
                    if mapped_task is not None:
                        # Task lifecycle is a fact even when the surrounding
                        # notification turn is not shown in this chat.
                        stream.publish(mapped_task)
                        if self._session_tasks is not None:
                            try:
                                await self._session_tasks.publish(
                                    turn.session_id, mapped_task
                                )
                            except Exception:
                                logger.exception(
                                    "failed to publish session task event session=%s",
                                    turn.session_id,
                                )
                        continue

                    if type(message).__name__ == "UserMessage":
                        kind = origin_kind(message)
                        if kind == "human":
                            injected = False
                        elif kind is not None and kind != "auto-continuation":
                            injected = True
                        # origin=None covers tool results and our own prompt;
                        # auto-continuation is displayed as part of this turn.
                        continue

                    if (
                        getattr(message, "subtype", "") == "init"
                        and isinstance(getattr(message, "data", None), dict)
                    ):
                        self._capture_init(turn.agent_id, message)
                        continue

                    if injected:
                        # Injected turns (task notifications, peer messages,
                        # ...) must not leak their text into this turn.
                        if type(message).__name__ == "ResultMessage":
                            injected = False
                        continue

                    parent_id = getattr(message, "parent_tool_use_id", None)
                    if parent_id:
                        parent_id = str(parent_id)
                        event = getattr(message, "event", None)
                        if isinstance(event, dict):
                            sub_mapper = subagent_mappers.get(parent_id)
                            if sub_mapper is None:
                                sub_mapper = StreamMapper()
                                subagent_mappers[parent_id] = sub_mapper
                            for mapped in sub_mapper.feed(event):
                                if mapped["type"] == "assistant_delta":
                                    stream.publish(
                                        {
                                            "type": "subagent_text",
                                            "parent_tool_use_id": parent_id,
                                            "text": mapped["delta"],
                                        }
                                    )
                                    subagent_streamed.add(parent_id)
                                elif mapped["type"] == "tool_call":
                                    artifact = artifact_from_tool(
                                        mapped["name"], mapped["input"], workspace
                                    )
                                    if artifact is not None:
                                        mapped["artifact"] = artifact
                                    mapped["type"] = "subagent_tool_call"
                                    mapped["parent_tool_use_id"] = parent_id
                                    stream.publish(mapped)
                                    subagent_streamed.add(parent_id)
                                # Subagent thinking is dropped to keep the
                                # live trace readable.
                        elif (
                            parent_id not in subagent_streamed
                            and type(message).__name__ == "AssistantMessage"
                        ):
                            # Complete-message fallback for CLIs that do not
                            # emit partial events for subagents.
                            for mapped in subagent_message_events(message):
                                if mapped["type"] == "subagent_tool_call":
                                    artifact = artifact_from_tool(
                                        mapped["name"], mapped["input"], workspace
                                    )
                                    if artifact is not None:
                                        mapped["artifact"] = artifact
                                stream.publish(mapped)
                        continue

                    event = getattr(message, "event", None)
                    if isinstance(event, dict):
                        for mapped in mapper.feed(event):
                            if mapped["type"] == "assistant_delta":
                                text_parts.append(mapped["delta"])
                            elif mapped["type"] == "tool_call":
                                artifact = artifact_from_tool(
                                    mapped["name"], mapped["input"], workspace
                                )
                                if artifact is not None:
                                    mapped["artifact"] = artifact
                            stream.publish(mapped)
                        continue
                    # SystemMessage also carries .subtype; match the class name so
                    # the init frame is not mistaken for a result.
                    if type(message).__name__ != "ResultMessage":
                        continue
                    # Background task notifications can surface as result
                    # messages too; keep draining until this turn's own result.
                    kind = origin_kind(message)
                    if kind not in (None, "human"):
                        injected = False
                        continue
                    injected = False
                    outcome = outcome_from_result(
                        message, fallback_text="".join(text_parts)
                    )
                    result_received = True
                    # The CLI exits after an error result, so a cached client
                    # would only fail again on the next turn.
                    if outcome.status != "completed":
                        evict = True
                    # Stop here: this turn's SSE stream closes with turn_end.
                    # Any background-task frames that arrive later stay in the
                    # client's message buffer and are published by the next
                    # turn that reads it; eviction before then drops them.
                    break
        except TimeoutError:
            outcome = TurnOutcome(
                status="failed",
                output="".join(text_parts),
                error="run exceeded the execution timeout",
            )
            evict = True
        except asyncio.CancelledError:
            outcome = TurnOutcome(status="cancelled", output="".join(text_parts))
            evict = True
            keep_lease = True
            raise
        except Exception as exc:
            if _is_signal_exit(exc):
                logger.warning(
                    "claude process stopped by signal; keeping lease session=%s agent=%s",
                    turn.session_id,
                    turn.agent_id,
                )
                outcome = TurnOutcome(
                    status="cancelled", output="".join(text_parts)
                )
                evict = True
                keep_lease = True
            else:
                logger.exception(
                    "claude turn failed session=%s agent=%s",
                    turn.session_id,
                    turn.agent_id,
                )
                outcome = TurnOutcome(
                    status="failed",
                    output="".join(text_parts),
                    error=_redact_cli_line(str(exc)),
                )
                evict = True
        finally:
            # Snapshot before clearing: an interrupt without a result owes no
            # channel reply. The marker is cleared at the end of teardown so the
            # delivery decision can still see it.
            interrupted = key in self._cancel_requested
            if not result_received:
                evict = True
            # Attach the between-turns reader while the client is still marked
            # active: a sweep that saw it idle before the reader started could
            # otherwise evict it and mark its tasks interrupted underneath us.
            # Starting while active is safe; once active is cleared below, a
            # later eviction stops the reader before marking anything.
            if result_received and not evict and not keep_lease:
                cached = self._clients.get(key)
                if cached is not None:
                    self._start_tail_reader(key, cached)
            self._active.discard(key)
            self._query_sent.discard(key)
            if self._active_turns.get(key) == turn.id:
                self._active_turns.pop(key, None)
            self._touch(key)
            # A local CLI slash command (and any turn the CLI did not stream)
            # only reports its text in the result. Publish it once so the chat
            # is not empty; streamed turns already collected it in text_parts.
            if not text_parts and outcome.output.strip():
                stream.publish(
                    {"type": "assistant_delta", "delta": outcome.output}
                )
            # Publish teardown synchronously first: a later await must not be
            # able to delay or suppress the terminal event for subscribers.
            stream.publish(
                {
                    "type": "turn_end",
                    "status": outcome.status,
                    # Result-message errors are formed outside the exception
                    # handlers above; redact at the single publish point so no
                    # error source can put the Amap key into the chat.
                    "error": _redact_cli_line(outcome.error)
                    if outcome.error
                    else outcome.error,
                    "terminal_reason": outcome.terminal_reason,
                    "usage": outcome.usage,
                }
            )
            stream.close()
            if evict:
                if defer_eviction:
                    # The registry drains this after releasing its concurrency
                    # permit; a slow CLI disconnect must not hold the slot.
                    self._evict_pending.add(key)
                else:
                    await self._evict_key(key)
            if lease_added and not keep_lease:
                if self._session_tasks is not None:
                    try:
                        await self._session_tasks.flush(turn.session_id)
                    except BaseException as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            logger.warning(
                                "task flush cancelled session=%s", turn.session_id
                            )
                        else:
                            logger.warning(
                                "failed to flush session tasks session=%s",
                                turn.session_id,
                                exc_info=True,
                            )
                try:
                    await turn_lease.end_turn(turn.session_id)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "turn lease end cancelled session=%s", turn.session_id
                        )
                    else:
                        logger.warning(
                            "failed to end turn lease session=%s",
                            turn.session_id,
                            exc_info=True,
                        )
            # Delivery teardown. Shutdown cancellation keeps the lease for
            # recovery, so the obligation stays empty and pending there and the
            # resumed turn reattaches it; sending now could double reply or mark
            # it sent and lose the answer. Failures here must never mask the
            # terminal event or break lease cleanup.
            if delivery_id is not None and not keep_lease:
                content = _delivery_content(
                    outcome,
                    interrupted=interrupted,
                    result_received=result_received,
                )
                if content is None:
                    # The user stopped the turn, so no reply is owed; drop the
                    # empty obligation instead of leaving a stale row behind.
                    await _best_effort(
                        lambda: channel_delivery.discard(delivery_id),
                        f"delivery discard delivery={delivery_id}",
                    )
                else:
                    completed = await _best_effort(
                        lambda: channel_delivery.complete(delivery_id, content),
                        f"delivery completion delivery={delivery_id}",
                    )
                    if completed and self._deliver_callback is not None:
                        await _best_effort(
                            lambda: self._deliver_callback(delivery_id),
                            f"delivery send delivery={delivery_id}",
                        )
            if (
                delivery_id is None
                and not keep_lease
                and turn.delivery is not None
                and turn.delivery.delivery_id is not None
            ):
                # The pre-created queued row was never adopted (the turn
                # aborted before starting), so no answer is owed and the row
                # must not be left for startup to answer.
                await _best_effort(
                    lambda: channel_delivery.discard(turn.delivery.delivery_id),
                    f"queued delivery discard delivery={turn.delivery.delivery_id}",
                )
            self._cancel_requested.discard(key)
        return outcome

    async def after_turn(self, session_id: str, agent_id: str | None = None) -> None:
        """Drain evictions deferred by ``run`` after the permit is released."""
        keys = [
            key
            for key in list(self._evict_pending)
            if key[1] == session_id and (agent_id is None or key[0] == agent_id)
        ]
        for key in keys:
            try:
                await self._evict_key(key)
            except asyncio.CancelledError:
                # Keep the key for the next after_turn; the client was not
                # evicted and must not be reused silently.
                raise
            self._evict_pending.discard(key)

    async def interrupt(
        self,
        session_id: str,
        agent_id: str | None = None,
        turn_id: str | None = None,
    ) -> bool:
        lock = self._client_lock(session_id)
        keys = [
            key
            for key in self._keys_for_session(session_id)
            if agent_id is None or key[0] == agent_id
        ]
        if agent_id is not None and not keys:
            keys = [(agent_id, session_id)]

        def matching() -> list[ClientKey]:
            return [
                key
                for key in keys
                if key in self._active
                and (turn_id is None or self._active_turns.get(key) == turn_id)
            ]

        try:
            async with asyncio.timeout(_INTERRUPT_LOCK_TIMEOUT_SECONDS):
                async with lock:
                    active = matching()
                    if not active:
                        return False
                    for key in active:
                        self._cancel_requested.add(key)
        except TimeoutError:
            # The session may have started or ended while this call waited.
            keys = [
                key
                for key in self._keys_for_session(session_id)
                if agent_id is None or key[0] == agent_id
            ]
            if agent_id is not None and not keys:
                keys = [(agent_id, session_id)]
            active = matching()
            logger.warning(
                "interrupt lock timed out session=%s agent=%s",
                session_id,
                agent_id,
            )
            if not active:
                return False
            for key in active:
                self._cancel_requested.add(key)
        target = next(
            (
                (key, cached.client)
                for key, cached in self._clients.items()
                if key[1] == session_id
                and (agent_id is None or key[0] == agent_id)
                and (turn_id is None or self._active_turns.get(key) == turn_id)
            ),
            None,
        )
        if target is not None:
            target_key, client = target
            # Before the prompt is written, an interrupt control request can
            # reach the CLI with nothing to abort. The runner re-sends it after
            # query() returns, so only send it here once the prompt is in
            # flight.
            if target_key in self._query_sent:
                try:
                    await asyncio.wait_for(client.interrupt(), timeout=5)
                except Exception:
                    # The control request failed, so the turn is not actually
                    # cancelled; do not leave it marked as interrupted.
                    self._cancel_requested.discard(target_key)
                    logger.warning(
                        "interrupt failed session=%s",
                        session_id,
                        exc_info=True,
                    )
                    return False
        return True

    async def _disconnect(
        self, client: Any, session_id: str, mark_tasks: bool = True
    ) -> bool:
        """Disconnect one client within the shared timeout.

        A timed-out or failed client is retained in ``_orphaned_clients``:
        timing out raw-cancels the SDK's shielded cleanup, skipping its
        SIGTERM/SIGKILL escalation, and an exception may leave the CLI child
        alive, so the reference is never silently dropped.

        The session's background tasks are marked interrupted before the
        disconnect starts, because this client is the only reader of their
        events and an early mark also covers a timeout, a failed close or an
        outer cancellation. Callers that already marked under the session lock
        pass ``mark_tasks=False``.
        """
        if mark_tasks and self._session_tasks is not None:
            try:
                await self._session_tasks.mark_interrupted(
                    session_id, "SDK client 已断开"
                )
            except Exception:
                logger.exception(
                    "failed to mark background tasks interrupted session=%s",
                    session_id,
                )
        disconnected = False
        try:
            async with asyncio.timeout(_DISCONNECT_TIMEOUT_SECONDS):
                await client.disconnect()
            disconnected = True
        except TimeoutError:
            logger.warning(
                "client disconnect timed out session=%s; keeping reference",
                session_id,
            )
            self._retain_orphan(client)
        except Exception:
            logger.exception("client disconnect failed session=%s", session_id)
            self._retain_orphan(client)
        return disconnected

    async def _evict_key(
        self, key: ClientKey, *, expected: _CachedClient | None = None
    ) -> bool:
        """Evict one cached client if it is still the one we meant to remove.

        The check, the task-interruption mark and the pop happen under the
        session lock, so a turn starting during a prior entry's disconnect
        can neither be disconnected by a stale sweep snapshot nor publish new
        tasks between the mark and the pop and have them marked interrupted.
        The mark runs before the pop so a cancellation while marking leaves the
        client cached instead of silently dropping it, and before the (slow)
        disconnect to cover its timeout and cancellation paths as well.
        """
        session_id = key[1]
        lock = self._client_lock(session_id)
        async with lock:
            if key in self._active:
                return False
            cached = self._clients.get(key)
            if cached is None or (expected is not None and cached is not expected):
                return False
            # Stop the tail reader before the interruption mark: once it
            # returns, no task event can land after the tasks are marked.
            await self._stop_tail_reader(cached)
            if self._session_tasks is not None:
                try:
                    await self._session_tasks.mark_interrupted(
                        session_id, "SDK client 已断开"
                    )
                except Exception:
                    logger.exception(
                        "failed to mark background tasks interrupted session=%s",
                        session_id,
                    )
            self._clients.pop(key, None)
        try:
            await self._disconnect(cached.client, session_id, mark_tasks=False)
        except asyncio.CancelledError:
            # The entry is already out of the cache; retain the reference
            # so the shutdown path can still report/close the CLI.
            self._retain_orphan(cached.client)
            raise
        return True

    def _retain_orphan(self, client: Any) -> None:
        if any(item is client for item in self._orphaned_clients):
            return
        self._orphaned_clients.append(client)
        overflow = len(self._orphaned_clients) - _MAX_ORPHANED_CLIENTS
        if overflow > 0:
            logger.warning(
                "orphan client list exceeded %d; dropping %d oldest reference(s)",
                _MAX_ORPHANED_CLIENTS,
                overflow,
            )
            del self._orphaned_clients[:overflow]

    async def _retry_orphans(self) -> int:
        """Give retained clients one more bounded disconnect attempt."""
        released = 0
        deadline = asyncio.get_running_loop().time() + _ORPHAN_RETRY_BUDGET_SECONDS
        for client in list(self._orphaned_clients):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning("orphan retry budget exhausted; keeping references")
                break
            try:
                async with asyncio.timeout(
                    min(_DISCONNECT_TIMEOUT_SECONDS, remaining)
                ):
                    await client.disconnect()
            except TimeoutError:
                logger.warning("orphan client still cannot disconnect")
                continue
            except Exception:
                logger.exception("orphan client disconnect failed")
                continue
            self._orphaned_clients = [
                item for item in self._orphaned_clients if item is not client
            ]
            released += 1
        if released:
            logger.info("released %d orphaned SDK client(s)", released)
        return released

    async def _evict(self, session_id: str) -> None:
        for key in self._keys_for_session(session_id):
            await self._evict_key(key)

    async def close(self, session_id: str) -> None:
        await self._evict(session_id)

    async def close_all(self) -> None:
        sweeper = self._sweeper
        self._sweeper = None
        if sweeper is not None:
            sweeper.cancel()
            try:
                await sweeper
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("client sweeper shutdown failed", exc_info=True)
        snapshot = list(self._clients.items())
        self._clients.clear()
        self._evict_pending.clear()
        deadline = asyncio.get_running_loop().time() + _SHUTDOWN_BUDGET_SECONDS
        # Disconnect every cached client concurrently: each may need the full
        # SDK close budget, so a sequential loop would exhaust the shutdown
        # budget after one or two slow clients.
        await asyncio.gather(
            *(
                self._disconnect_for_shutdown(
                    cached, key[1], deadline
                )
                for key, cached in snapshot
            )
        )
        if self._orphaned_clients:
            logger.warning(
                "%d SDK client(s) did not disconnect within the shutdown budget",
                len(self._orphaned_clients),
            )

    async def _disconnect_for_shutdown(
        self, cached: _CachedClient, session_id: str, deadline: float
    ) -> None:
        # Stop the tail reader and mark before the budget check: even a client
        # kept as an orphan cannot publish its background task events anymore.
        await self._stop_tail_reader(cached)
        if self._session_tasks is not None:
            try:
                await self._session_tasks.mark_interrupted(
                    session_id, "服务关机，SDK client 已断开"
                )
            except Exception:
                logger.exception(
                    "failed to mark background tasks interrupted session=%s",
                    session_id,
                )
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            logger.warning(
                "shutdown budget exhausted; keeping client reference session=%s",
                session_id,
            )
            self._retain_orphan(cached.client)
            return
        try:
            async with asyncio.timeout(
                min(_DISCONNECT_TIMEOUT_SECONDS, remaining)
            ):
                await cached.client.disconnect()
        except TimeoutError:
            # Timing out raw-cancels the SDK's shielded cleanup and skips its
            # SIGTERM/SIGKILL escalation; keep the reference so the CLI is not
            # silently orphaned in the cache and report it. The process exits
            # with ours.
            logger.warning(
                "client disconnect exceeded shutdown budget session=%s; "
                "keeping reference",
                session_id,
            )
            self._retain_orphan(cached.client)
        except Exception:
            logger.exception(
                "client disconnect failed during shutdown session=%s",
                session_id,
            )
            self._retain_orphan(cached.client)
