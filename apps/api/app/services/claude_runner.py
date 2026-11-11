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
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings as app_settings
from app.db import SessionLocal
from app.services import channel_delivery, sdk_bridge, turn_lease
from app.services.claude_env import build_claude_env, claude_paths, workspace_for
from app.services.identifiers import valid_agent_id
from app.services.sdk_events import StreamMapper, TurnOutcome, outcome_from_result
from app.services.settings_store import SettingsStore
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)

# Shared shutdown budget; bounds systemd ``TimeoutStopSec`` exposure.
_DISCONNECT_TIMEOUT_SECONDS = 10
# Total time ``close_all`` may spend disconnecting cached clients. The deploy
# unit allows 60 s for the whole shutdown, leaving room for the other runtimes.
_SHUTDOWN_BUDGET_SECONDS = 20
# A connect that wedges would otherwise hold a concurrency permit forever.
_CONNECT_TIMEOUT_SECONDS = 60
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


def _memory_stamp(home: Path | None) -> str:
    """Identity of the shared user profile loaded from ``claude/CLAUDE.md``."""
    path = claude_paths(home)["config"] / "CLAUDE.md"
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


def _config_fingerprint(
    agent: Any, memory_stamp: str = "", plugin_stamp: str = ""
) -> str:
    """Hash the settings that are baked into a connected SDK client."""
    provider = agent.provider
    payload = json.dumps(
        {
            "instructions": agent.instructions or "",
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": provider.api_key,
            "memory": memory_stamp,
            "plugins": plugin_stamp,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class DeliveryRequest:
    """Channel obligation to create when this turn starts.

    ``session_id`` comes from the turn; the row is filled with the final
    answer and handed to ``deliver_callback`` at teardown.
    """

    channel_id: str
    external_id: str


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
    ) -> None:
        self._store = store
        self._home = home
        # Keyed by (agent_id, session_id): a session id reused across Agents
        # must never reuse another Agent's workspace/instructions/credentials.
        self._clients: dict[ClientKey, _CachedClient] = {}
        self._factory = client_factory
        self._deliver_callback = deliver_callback
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: set[ClientKey] = set()
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

    def _keys_for_session(self, session_id: str) -> list[ClientKey]:
        keys = set(self._clients) | self._active | self._cancel_requested
        return [key for key in keys if key[1] == session_id]

    async def _client(self, turn: Turn, agent: Any) -> Any:
        lock = self._client_lock(turn.session_id)
        key = (agent.id, turn.session_id)
        memory_stamp = _memory_stamp(self._home)
        plugin_stamp = _plugin_stamp(self._home)
        async with lock:
            cached = self._clients.get(key)
            if cached is not None:
                fingerprint = _config_fingerprint(
                    agent, memory_stamp, plugin_stamp
                )
                if cached.fingerprint != fingerprint:
                    # The Agent or Provider changed while this session was
                    # busy; rebuild instead of running with stale settings.
                    self._clients.pop(key, None)
                    try:
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
                "setting_sources": ["user", "project"],
                "permission_mode": app_settings.claude_permission_mode,
                "max_buffer_size": _MAX_BUFFER_SIZE,
                # Surface CLI stderr at debug level so a failed turn is
                # diagnosable without exposing it to the chat.
                "stderr": lambda line: logger.debug(
                    "cli[%s] %s", turn.session_id, line
                ),
            }
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
                    agent, memory_stamp, plugin_stamp
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
        for key, cached in expired:
            if await self._evict_key(key, expected=cached):
                evicted += 1
        await self._enforce_capacity()
        await self._retry_orphans()
        if evicted:
            logger.info("evicted %d idle SDK client(s)", evicted)
        return evicted

    async def _enforce_capacity(self) -> None:
        overflow = len(self._clients) - max(1, self._max_clients)
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
        for _, key, cached in idle[:overflow]:
            await self._evict_key(key, expected=cached)

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
        self._touch(key)
        mapper = StreamMapper()
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
                # Lease and delivery obligation are written together so a
                # crash can never leave a turn marked running without the
                # reply it owes, or vice versa.
                async with SessionLocal() as db:
                    await turn_lease.add_in(db, turn.session_id)
                    # A resumed turn must attach to an obligation created before
                    # the crash, never create another one (that would double
                    # reply); the lookup below is its only source.
                    if turn.delivery is not None and turn.source != "resume":
                        delivery_id = await channel_delivery.create_pending(
                            db,
                            channel_id=turn.delivery.channel_id,
                            external_id=turn.delivery.external_id,
                            session_id=turn.session_id,
                        )
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
                client = await self._client(turn, agent)
                self._touch(key)
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
                # Drain the full stream: receive_response() would stop at the
                # first ResultMessage, which may be an injected background
                # task notification rather than this turn's result.
                async for message in client.receive_messages():
                    if getattr(message, "parent_tool_use_id", None):
                        continue
                    event = getattr(message, "event", None)
                    if isinstance(event, dict):
                        for mapped in mapper.feed(event):
                            if mapped["type"] == "assistant_delta":
                                text_parts.append(mapped["delta"])
                            stream.publish(mapped)
                        continue
                    # SystemMessage also carries .subtype; match the class name so
                    # the init frame is not mistaken for a result.
                    if type(message).__name__ != "ResultMessage":
                        continue
                    # Background task notifications can surface as result
                    # messages too; keep draining until this turn's own result.
                    origin = getattr(message, "origin", None)
                    kind = (
                        origin.get("kind")
                        if isinstance(origin, dict)
                        else getattr(origin, "kind", None)
                    )
                    if kind not in (None, "human"):
                        continue
                    outcome = outcome_from_result(
                        message, fallback_text="".join(text_parts)
                    )
                    result_received = True
                    # The CLI exits after an error result, so a cached client
                    # would only fail again on the next turn.
                    if outcome.status != "completed":
                        evict = True
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
            logger.exception(
                "claude turn failed session=%s agent=%s",
                turn.session_id,
                turn.agent_id,
            )
            outcome = TurnOutcome(
                status="failed", output="".join(text_parts), error=str(exc)
            )
            evict = True
        finally:
            # Snapshot before clearing: an interrupt without a result owes no
            # channel reply. The marker is cleared at the end of teardown so the
            # delivery decision can still see it.
            interrupted = key in self._cancel_requested
            self._active.discard(key)
            self._touch(key)
            if not result_received:
                evict = True
            # Publish teardown synchronously first: a later await must not be
            # able to delay or suppress the terminal event for subscribers.
            stream.publish(
                {
                    "type": "turn_end",
                    "status": outcome.status,
                    "error": outcome.error,
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
                try:
                    await turn_lease.remove(turn.session_id)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "turn lease removal cancelled session=%s", turn.session_id
                        )
                    else:
                        logger.warning(
                            "failed to remove turn lease session=%s",
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
            self._evict_pending.discard(key)
            await self._evict_key(key)

    async def interrupt(self, session_id: str, agent_id: str | None = None) -> bool:
        lock = self._client_lock(session_id)
        keys = [
            key
            for key in self._keys_for_session(session_id)
            if agent_id is None or key[0] == agent_id
        ]
        if agent_id is not None and not keys:
            keys = [(agent_id, session_id)]
        try:
            async with asyncio.timeout(_INTERRUPT_LOCK_TIMEOUT_SECONDS):
                async with lock:
                    # Re-check under the lock: the turn may have ended while
                    # this call waited, and a stale marker would cancel the
                    # session's next turn.
                    if not any(key in self._active for key in keys):
                        return False
                    # Remember the request so a turn still starting up aborts
                    # before it ever queries the client.
                    for key in keys:
                        self._cancel_requested.add(key)
        except TimeoutError:
            # A connect holding the lock must not make /stop hang forever; the
            # marker still makes the turn abort after it acquires the client.
            # Recompute keys because the session may have started or ended
            # while this call was waiting.
            keys = [
                key
                for key in self._keys_for_session(session_id)
                if agent_id is None or key[0] == agent_id
            ]
            if agent_id is not None and not keys:
                keys = [(agent_id, session_id)]
            logger.warning(
                "interrupt lock timed out session=%s agent=%s",
                session_id,
                agent_id,
            )
            if not any(key in self._active for key in keys):
                return False
            for key in keys:
                self._cancel_requested.add(key)
        client = next(
            (
                cached.client
                for key, cached in self._clients.items()
                if key[1] == session_id
                and (agent_id is None or key[0] == agent_id)
            ),
            None,
        )
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), timeout=5)
            except Exception:
                logger.warning(
                    "interrupt failed session=%s", session_id, exc_info=True
                )
        return True

    async def _disconnect(self, client: Any, session_id: str) -> bool:
        """Disconnect one client within the shared timeout.

        A timed-out client is retained in ``_orphaned_clients`` (the SDK has no
        process-termination API) so the reference is never silently dropped;
        exceptions are logged and treated as a completed teardown.
        """
        try:
            async with asyncio.timeout(_DISCONNECT_TIMEOUT_SECONDS):
                await client.disconnect()
            return True
        except TimeoutError:
            logger.warning(
                "client disconnect timed out session=%s; keeping reference",
                session_id,
            )
            self._retain_orphan(client)
            return False
        except Exception:
            logger.exception("client disconnect failed session=%s", session_id)
            return True

    async def _evict_key(
        self, key: ClientKey, *, expected: _CachedClient | None = None
    ) -> bool:
        """Evict one cached client if it is still the one we meant to remove.

        The check and the pop happen with no await between them while holding
        the session lock, so a turn starting during a prior entry's disconnect
        can never be disconnected by a stale sweep snapshot.
        """
        session_id = key[1]
        lock = self._client_lock(session_id)
        async with lock:
            if key in self._active:
                return False
            cached = self._clients.get(key)
            if cached is None or (expected is not None and cached is not expected):
                return False
            self._clients.pop(key, None)
        try:
            await self._disconnect(cached.client, session_id)
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
        for key, cached in snapshot:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning(
                    "shutdown budget exhausted; keeping client reference session=%s",
                    key[1],
                )
                self._retain_orphan(cached.client)
                continue
            try:
                async with asyncio.timeout(
                    min(_DISCONNECT_TIMEOUT_SECONDS, remaining)
                ):
                    await cached.client.disconnect()
            except TimeoutError:
                # The SDK client has no process-termination API; keep the
                # reference so the CLI is not silently orphaned in the cache
                # and report it. The process exits with ours.
                logger.warning(
                    "client disconnect exceeded shutdown budget session=%s; "
                    "keeping reference",
                    key[1],
                )
                self._retain_orphan(cached.client)
            except Exception:
                logger.exception(
                    "client disconnect failed during shutdown session=%s", key[1]
                )
        if self._orphaned_clients:
            logger.warning(
                "%d SDK client(s) did not disconnect within the shutdown budget",
                len(self._orphaned_clients),
            )
