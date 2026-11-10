"""Single-turn executor backed by the Claude Agent SDK.

One runner instance exists per process. It keeps a per-session
``ClaudeSDKClient`` cache so a session's next turn resumes the same SDK
conversation; the turn lease row marks which session currently has a turn
attached, and the rich ``turn_end`` event (status/error/usage) is published
here rather than by ``TurnStream.close``. Run persistence, queueing and
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
        settings = await self._store.get()
        agent = next((item for item in settings.agents if item.id == turn.agent_id), None)
        if agent is None:
            stream.publish({"type": "turn_error", "error": "agent not found"})
            stream.close()
            return TurnOutcome(status="failed", error="agent not found")
        await turn_lease.add(turn.session_id)
        mapper = StreamMapper()
        text_parts: list[str] = []
        outcome = TurnOutcome(status="failed", error="run ended without result")
        try:
            client = await self._client(turn, agent)
            if turn.source == "resume":
                prompt = (
                    "上次任务因服务重启被中断。请先检查当前状态，"
                    "继续完成原任务；已经完成的步骤不要重复执行。"
                )
            else:
                prompt = turn.prompt
            await client.query(prompt)
            async with asyncio.timeout(app_settings.run_timeout_seconds):
                async for message in client.receive_response():
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
                    if type(message).__name__ == "ResultMessage":
                        outcome = outcome_from_result(
                            message, fallback_text="".join(text_parts)
                        )
                        break
        except TimeoutError:
            outcome = TurnOutcome(status="failed", error="run exceeded the execution timeout")
            try:
                await self.interrupt(turn.session_id)
            except Exception:
                logger.exception("interrupt after timeout failed")
        except asyncio.CancelledError:
            outcome = TurnOutcome(status="cancelled", output="".join(text_parts))
            raise
        except Exception as exc:
            logger.exception("claude turn failed")
            outcome = TurnOutcome(status="failed", error=str(exc))
        finally:
            try:
                await turn_lease.remove(turn.session_id)
            except Exception:
                logger.exception("failed to remove turn lease session=%s", turn.session_id)
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
        return outcome

    async def interrupt(self, session_id: str) -> bool:
        client = self._clients.get(session_id)
        if client is None:
            return False
        await client.interrupt()
        return True

    async def close(self, session_id: str) -> None:
        client = self._clients.pop(session_id, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("client disconnect failed")
