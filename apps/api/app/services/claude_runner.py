"""Single-turn executor backed by the Claude Agent SDK.

One runner instance exists per process. It keeps a per-session
``ClaudeSDKClient`` cache so a session's next turn resumes the same SDK
conversation; the turn lease row marks which session currently has a turn
attached, and the rich ``turn_end`` event (status/error/usage) is published
here rather than by ``TurnStream.close``. A client is evicted after timeouts,
failures and streams that end without this turn's own result, so a poisoned
stream cannot leak into the next turn. Run persistence, queueing and
cancellation policy stay outside this module.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import settings as app_settings
from app.services import sdk_bridge, turn_lease
from app.services.claude_env import build_claude_env, claude_paths, workspace_for
from app.services.sdk_events import StreamMapper, TurnOutcome, outcome_from_result
from app.services.settings_store import SettingsStore
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    session_id: str
    agent_id: str
    prompt: str
    source: str = "manual"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


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
    ) -> None:
        self._store = store
        self._home = home
        self._clients: dict[str, Any] = {}
        self._factory = client_factory
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: set[str] = set()
        self._cancel_requested: set[str] = set()

    def _client_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def _client(self, turn: Turn, agent: Any) -> Any:
        lock = self._client_lock(turn.session_id)
        async with lock:
            client = self._clients.get(turn.session_id)
            if client is not None:
                return client
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
            }
            if exists:
                kwargs["resume"] = turn.session_id
            else:
                kwargs["session_id"] = turn.session_id
            options = options_class(**kwargs)
            if self._factory is not None:
                client = self._factory(options)
            else:
                client = sdk_bridge.claude_client_class()(options)
            await client.connect()
            self._clients[turn.session_id] = client
            return client

    async def run(self, turn: Turn, stream: TurnStream) -> TurnOutcome:
        # Mark active before any await so /stop can reach a turn that is still
        # loading settings or connecting its client.
        self._active.add(turn.session_id)
        mapper = StreamMapper()
        text_parts: list[str] = []
        outcome = TurnOutcome(status="failed", error="run ended without result")
        lease_added = False
        result_received = False
        evict = False
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
                await turn_lease.add(turn.session_id)
                client = await self._client(turn, agent)
                if turn.source == "resume":
                    prompt = (
                        "上次任务因服务重启被中断。请先检查当前状态，"
                        "继续完成原任务；已经完成的步骤不要重复执行。"
                    )
                else:
                    prompt = turn.prompt
                if turn.session_id in self._cancel_requested:
                    self._cancel_requested.discard(turn.session_id)
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
            self._active.discard(turn.session_id)
            self._cancel_requested.discard(turn.session_id)
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
                try:
                    await asyncio.shield(self._evict(turn.session_id))
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        logger.warning(
                            "client eviction cancelled session=%s", turn.session_id
                        )
                    else:
                        logger.warning(
                            "client eviction failed session=%s",
                            turn.session_id,
                            exc_info=True,
                        )
            if lease_added:
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
        return outcome

    async def interrupt(self, session_id: str) -> bool:
        lock = self._client_lock(session_id)
        async with lock:
            if session_id not in self._active:
                return False
            # Remember the request so a turn still starting up aborts before
            # it ever queries the client.
            self._cancel_requested.add(session_id)
            client = self._clients.get(session_id)
        if client is not None:
            try:
                await asyncio.wait_for(client.interrupt(), timeout=5)
            except Exception:
                logger.warning(
                    "interrupt failed session=%s", session_id, exc_info=True
                )
        return True

    async def _evict(self, session_id: str) -> None:
        lock = self._client_lock(session_id)
        async with lock:
            client = self._clients.pop(session_id, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("client disconnect failed session=%s", session_id)

    async def close(self, session_id: str) -> None:
        await self._evict(session_id)

    async def close_all(self) -> None:
        for session_id in list(self._clients):
            await self.close(session_id)
