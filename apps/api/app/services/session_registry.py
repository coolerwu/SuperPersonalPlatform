"""In-process turn registry with per-session serialization.

Turns submitted for the same session are queued and executed one at a time by
a dedicated session worker, preserving conversation order; turns from
different sessions run concurrently, bounded by a shared semaphore. Each turn
owns its own ``TurnStream`` keyed by the turn id, so queued turns can be
subscribed before they start and one turn's completion never closes another
turn's stream. Serial execution per session (one running turn per session) is
the correctness invariant: the SDK client and checkpoint for a session are only
safe to drive one turn at a time.

Run persistence, cancellation bookkeeping and SSE delivery live outside this
module; ``interrupt`` simply delegates to the runner.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.claude_runner import Turn
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)


class SessionRegistry:
    def __init__(self, runner: Any, *, concurrency: int = 5) -> None:
        self._runner = runner
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._queues: dict[str, asyncio.Queue[tuple[Turn, TurnStream]]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._streams: dict[str, TurnStream] = {}
        self._current: dict[str, str] = {}
        self._running: set[str] = set()
        self._max_streams = 256

    async def submit(self, turn: Turn) -> str:
        queue = self._queues.get(turn.session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[turn.session_id] = queue
            self._workers[turn.session_id] = asyncio.create_task(
                self._session_loop(turn.session_id), name=f"session-{turn.session_id[:8]}"
            )
        stream = TurnStream()
        self._streams[turn.id] = stream
        while len(self._streams) > self._max_streams:
            oldest = next(iter(self._streams))
            self._streams.pop(oldest, None)
        await queue.put((turn, stream))
        return turn.id

    async def _session_loop(self, session_id: str) -> None:
        queue = self._queues[session_id]
        while True:
            turn, stream = await queue.get()
            self._current[session_id] = turn.id
            self._running.add(session_id)
            try:
                async with self._semaphore:
                    await self._runner.run(turn, stream)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("session turn failed session=%s", session_id)
                stream.close()
            finally:
                self._running.discard(session_id)
                self._current.pop(session_id, None)
                queue.task_done()

    def stream(self, turn_id: str) -> TurnStream | None:
        return self._streams.get(turn_id)

    def current_turn_id(self, session_id: str) -> str | None:
        return self._current.get(session_id)

    def is_busy(self, session_id: str) -> bool:
        queue = self._queues.get(session_id)
        return session_id in self._running or (queue is not None and not queue.empty())

    async def interrupt(self, session_id: str) -> bool:
        return bool(await self._runner.interrupt(session_id))

    async def close(self) -> None:
        for task in self._workers.values():
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._streams.clear()
