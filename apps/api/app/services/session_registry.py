"""In-process turn registry with per-session serialization.

Turns submitted for the same session are queued and executed one at a time by
a dedicated session worker, preserving conversation order; turns from
different sessions run concurrently, bounded by a shared semaphore. Each turn
owns its own ``TurnStream`` keyed by the turn id, so queued turns can be
subscribed before they start and one turn's completion never closes another
turn's stream. Serial execution per session (one running turn per session) is
the correctness invariant: the SDK client and checkpoint for a session are only
safe to drive one turn at a time.

``interrupt`` backs ``/stop``: it drains the session's queued turns, publishing
a cancelled terminal event for each, and asks the runner to interrupt the turn
that is currently attached to the SDK client. A turn that has already left the
queue but is still waiting for the global concurrency permit is not cancelled;
that window is tiny and acceptable because the registry runs in a single
process. Idle workers retire once their queue is empty.

Run persistence and SSE delivery live outside this module.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.claude_runner import Turn
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)


class SessionRegistry:
    def __init__(
        self,
        runner: Any,
        *,
        concurrency: int = 5,
        max_streams: int = 256,
    ) -> None:
        self._runner = runner
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._queues: dict[str, asyncio.Queue[tuple[Turn, TurnStream]]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._streams: dict[str, TurnStream] = {}
        self._current: dict[str, str] = {}
        self._running: set[str] = set()
        self._max_streams = max(1, max_streams)
        self._closed = False

    async def submit(self, turn: Turn) -> str:
        if self._closed:
            raise RuntimeError("registry is closed")
        # This method intentionally has no awaits: queue creation, stream
        # registration and enqueue must be atomic with respect to a worker
        # retiring itself after seeing its queue empty.
        queue = self._queues.get(turn.session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[turn.session_id] = queue
            self._workers[turn.session_id] = asyncio.create_task(
                self._session_loop(turn.session_id),
                name=f"session-{turn.session_id[:8]}",
            )
        stream = TurnStream()
        self._streams[turn.id] = stream
        self._evict_closed_streams()
        queue.put_nowait((turn, stream))
        return turn.id

    def _evict_closed_streams(self) -> None:
        # Only finished streams are reclaimable. A queued or running turn must
        # keep its stream so a reconnect can still attach; allow the map to
        # exceed the cap while no closed stream is available to evict.
        while len(self._streams) > self._max_streams:
            oldest_closed = next(
                (
                    turn_id
                    for turn_id, stream in self._streams.items()
                    if stream.closed
                ),
                None,
            )
            if oldest_closed is None:
                break
            self._streams.pop(oldest_closed, None)

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
                logger.exception(
                    "session turn failed session=%s turn=%s", session_id, turn.id
                )
                stream.close()
            finally:
                self._running.discard(session_id)
                self._current.pop(session_id, None)
                queue.task_done()
            if queue.empty():
                # Retire atomically with the empty check and with submit (which
                # never awaits), so a new turn can never land in a queue whose
                # worker has already exited.
                self._queues.pop(session_id, None)
                self._workers.pop(session_id, None)
                return

    def stream(self, turn_id: str) -> TurnStream | None:
        return self._streams.get(turn_id)

    def current_turn_id(self, session_id: str) -> str | None:
        return self._current.get(session_id)

    def is_busy(self, session_id: str) -> bool:
        queue = self._queues.get(session_id)
        return session_id in self._running or (queue is not None and not queue.empty())

    async def interrupt(self, session_id: str) -> bool:
        cancelled = False
        queue = self._queues.get(session_id)
        if queue is not None:
            while True:
                try:
                    _, stream = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                stream.publish({"type": "turn_end", "status": "cancelled"})
                stream.close()
                queue.task_done()
                cancelled = True
        if cancelled and session_id not in self._running:
            # The worker is idle (or never started) and would otherwise stay
            # blocked on an empty queue forever. Retire it before any await.
            worker = self._workers.pop(session_id, None)
            if worker is not None:
                worker.cancel()
            self._queues.pop(session_id, None)
            self._current.pop(session_id, None)
        interrupted = bool(await self._runner.interrupt(session_id))
        return cancelled or interrupted

    async def close(self) -> None:
        self._closed = True
        for queue in self._queues.values():
            while True:
                try:
                    _, stream = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                stream.publish({"type": "turn_end", "status": "cancelled"})
                stream.close()
                queue.task_done()
        tasks = list(self._workers.values())
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), 10
                )
        except TimeoutError:
            logger.warning("session registry close timed out after 10s")
        finally:
            self._workers.clear()
            self._queues.clear()
            self._current.clear()
            self._running.clear()
            self._streams.clear()
