"""In-process turn registry with per-session serialization.

Turns submitted for the same session are queued and executed one at a time by
a dedicated session worker, preserving conversation order; turns from
different sessions run concurrently, bounded by a shared semaphore. Each turn
owns its own ``TurnStream`` keyed by the turn id, so queued turns can be
subscribed before they start and one turn's completion never closes another
turn's stream. Serial execution per session (one running turn per session) is
the correctness invariant: the SDK client and checkpoint for a session are only
safe to drive one turn at a time.

``interrupt`` backs ``/stop``: it cancels a turn that has been dequeued but is
still waiting for the global concurrency permit, drains the session's queued
turns (publishing a cancelled terminal event for each), and asks the runner to
interrupt a turn that is already running. Together with the runner's startup
cancellation check this covers both the queue/semaphore window and the
settings/client-connect window. Idle workers retire once their queue is empty.

Run persistence and SSE delivery live outside this module.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.services import channel_delivery
from app.services.claude_runner import Turn
from app.services.turn_stream import TurnStream

logger = logging.getLogger(__name__)

# Saving an Agent setting closes every idle client for that Agent. Bound the
# whole loop because a wedged CLI disconnect already has its own timeout.
_AGENT_CLOSE_BUDGET_SECONDS = 20.0


async def _discard_delivery(turn: Turn) -> None:
    """Drop a queued obligation whose turn the user cancelled before it ran."""
    if turn.delivery is None or turn.delivery.delivery_id is None:
        return
    try:
        await channel_delivery.discard(turn.delivery.delivery_id)
    except Exception:
        logger.warning(
            "failed to discard queued delivery delivery=%s",
            turn.delivery.delivery_id,
            exc_info=True,
        )


class SessionDeletingError(RuntimeError):
    """A turn was submitted while its session was being deleted."""


class AgentDeletingError(SessionDeletingError):
    """A turn was submitted while its Agent was being deleted."""


@dataclass(frozen=True)
class InterruptResult:
    """Outcome of ``/stop``: whether anything stopped, and what exactly.

    ``cancelled_turn_ids`` lets the web client drop only the optimistic
    messages whose turns really were cancelled; a turn that raced in after the
    drain keeps running and must stay visible. Truthiness mirrors the old
    boolean return value for callers that only ask "did this stop anything?".
    """

    cancelled: bool = False
    cancelled_turn_ids: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.cancelled


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
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._streams: dict[str, TurnStream] = {}
        self._turn_sessions: dict[str, str] = {}
        self._pending: dict[str, tuple[Turn, TurnStream]] = {}
        self._current: dict[str, str] = {}
        self._running: set[str] = set()
        # Workers in the after_turn teardown (deferred eviction) are not
        # cancellable by a user stop; cancelling them would drop the eviction.
        self._finishing: set[str] = set()
        self._started: set[str] = set()
        self._cancelled: set[str] = set()
        # Session -> Agent, so destructive Agent operations can tell whether a
        # queued turn (which may not have created a transcript yet) is active.
        # Entries survive worker retirement: an idle session may still hold a
        # cached SDK client that a settings change needs to close.
        # ``close_session`` removes them.
        self._session_agents: dict[str, str] = {}
        # Sessions whose delete has started. Unlike ``is_busy``, the check and
        # the marker are set without an await, so a turn cannot slip into the
        # queue between the check and the transcript delete.
        self._deleting: set[str] = set()
        # Agent-level delete marker; also blocks channel/cron turns for the
        # Agent while its workspace/transcripts are being removed.
        self._deleting_agents: set[str] = set()
        self._max_streams = max(1, max_streams)
        self._closed = False

    async def submit(self, turn: Turn) -> str:
        if self._closed:
            raise RuntimeError("registry is closed")
        if turn.agent_id in self._deleting_agents:
            raise AgentDeletingError("agent is being deleted")
        if turn.session_id in self._deleting:
            raise SessionDeletingError("session is being deleted")
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
        self._session_agents[turn.session_id] = turn.agent_id
        stream = TurnStream()
        self._streams[turn.id] = stream
        self._turn_sessions[turn.id] = turn.session_id
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
            self._turn_sessions.pop(oldest_closed, None)

    async def _session_loop(self, session_id: str) -> None:
        queue = self._queues[session_id]
        while True:
            turn, stream = await queue.get()
            self._current[session_id] = turn.id
            self._pending[session_id] = (turn, stream)
            self._running.add(session_id)
            try:
                try:
                    async with self._semaphore:
                        self._started.add(session_id)
                        skip = turn.id in self._cancelled
                        self._cancelled.discard(turn.id)
                        if not skip:
                            await self._runner.run(
                                turn, stream, defer_eviction=True
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "session turn failed session=%s turn=%s", session_id, turn.id
                    )
                    stream.close()
            finally:
                self._started.discard(session_id)
                self._pending.pop(session_id, None)
                self._cancelled.discard(turn.id)
                self._running.discard(session_id)
                self._current.pop(session_id, None)
                queue.task_done()
                self._finishing.add(session_id)
                try:
                    # A slow CLI disconnect must not hold the concurrency permit;
                    # the runner deferred it until now.
                    after_turn = getattr(self._runner, "after_turn", None)
                    if after_turn is not None:
                        try:
                            await after_turn(turn.session_id, turn.agent_id)
                        except Exception:
                            logger.exception(
                                "deferred client eviction failed session=%s",
                                session_id,
                            )
                finally:
                    self._finishing.discard(session_id)
            self._evict_closed_streams()
            if queue.empty():
                # Retire atomically with the empty check and with submit (which
                # never awaits), so a new turn can never land in a queue whose
                # worker has already exited. Keep the session -> agent map: an
                # idle session may still hold a cached SDK client, and Agent
                # settings changes need to find it.
                self._queues.pop(session_id, None)
                self._workers.pop(session_id, None)
                return

    def stream(self, turn_id: str, session_id: str | None = None) -> TurnStream | None:
        if session_id is not None and self._turn_sessions.get(turn_id) != session_id:
            return None
        return self._streams.get(turn_id)

    async def close_session(self, session_id: str) -> None:
        """Release the runner's cached SDK client after transcript deletion."""
        self._session_agents.pop(session_id, None)
        await self._runner.close(session_id)

    def begin_delete(self, session_id: str) -> bool:
        """Atomically mark a session for deletion.

        Returns False when a turn is queued or running, so the caller must not
        delete the transcript under it. Once True, ``submit`` rejects new turns
        for this session until ``finish_delete`` runs.
        """
        if self.is_busy(session_id) or session_id in self._deleting:
            return False
        self._deleting.add(session_id)
        return True

    def finish_delete(self, session_id: str) -> None:
        self._deleting.discard(session_id)

    def begin_agent_delete(self, agent_id: str) -> bool:
        """Atomically mark an Agent for permanent deletion.

        Returns False when any of its sessions has a queued/running turn.
        While set, every submit for the Agent is rejected, closing the window
        between the busy check and workspace/transcript removal.
        """
        if self.is_agent_busy(agent_id) or agent_id in self._deleting_agents:
            return False
        self._deleting_agents.add(agent_id)
        return True

    def finish_agent_delete(self, agent_id: str) -> None:
        self._deleting_agents.discard(agent_id)

    def session_agent(self, session_id: str) -> str | None:
        return self._session_agents.get(session_id)

    async def close_agent_clients(self, agent_id: str) -> int:
        """Close cached SDK clients for one Agent's idle sessions.

        Called after Agent instructions or provider settings change so the
        next turn rebuilds its client with the new configuration. Busy
        sessions are left alone; their next turn starts from the old client
        until it finishes and the settings change can be retried.
        """
        closed = 0
        deadline = asyncio.get_running_loop().time() + _AGENT_CLOSE_BUDGET_SECONDS
        for session_id, owner in list(self._session_agents.items()):
            if owner != agent_id or self.is_busy(session_id):
                continue
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning(
                    "client close budget exhausted agent=%s; leaving the rest cached",
                    agent_id,
                )
                break
            try:
                async with asyncio.timeout(remaining):
                    await self._runner.close(session_id)
            except TimeoutError:
                logger.warning(
                    "client close budget exceeded agent=%s session=%s",
                    agent_id,
                    session_id,
                )
                break
            closed += 1
        return closed

    def current_turn_id(self, session_id: str) -> str | None:
        return self._current.get(session_id)

    def is_busy(self, session_id: str) -> bool:
        queue = self._queues.get(session_id)
        return session_id in self._running or (queue is not None and not queue.empty())

    def is_agent_busy(self, agent_id: str) -> bool:
        """Whether any queued or running turn belongs to ``agent_id``.

        Used before archiving or permanently deleting an Agent. A queued turn
        may not have a transcript yet, so checking transcript files alone is
        not enough.
        """
        return any(
            self.is_busy(session_id)
            for session_id, owner in self._session_agents.items()
            if owner == agent_id
        )

    async def interrupt(self, session_id: str) -> InterruptResult:
        cancelled = False
        dropped: list[Turn] = []
        pending = self._pending.get(session_id)
        if pending is not None and session_id not in self._started:
            # The worker dequeued this turn but is still waiting for the global
            # concurrency permit. Close its stream now; the worker will skip it
            # once it acquires the permit and sees it in _cancelled.
            turn, stream = pending
            self._cancelled.add(turn.id)
            stream.publish({"type": "turn_end", "status": "cancelled"})
            stream.close()
            dropped.append(turn)
            cancelled = True
        queue = self._queues.get(session_id)
        if queue is not None:
            while True:
                try:
                    queued_turn, stream = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                stream.publish({"type": "turn_end", "status": "cancelled"})
                stream.close()
                queue.task_done()
                dropped.append(queued_turn)
                cancelled = True
        # Bind the interrupt to the turn that is running right now, before the
        # delivery-discard awaits below. Otherwise the running turn could end
        # and a new turn start in the same session during those awaits, and
        # /stop would cancel the new turn instead.
        running_turn_id = self._current.get(session_id)
        if (
            cancelled
            and session_id not in self._running
            and session_id not in self._finishing
        ):
            # The worker is idle (or never started) and would otherwise stay
            # blocked on an empty queue forever. Retire it before any await.
            worker = self._workers.pop(session_id, None)
            if worker is not None:
                worker.cancel()
            self._queues.pop(session_id, None)
            self._current.pop(session_id, None)
        # These turns never started, so their pre-created delivery rows still
        # exist; a turn the user stopped owes no reply.
        for turn in dropped:
            await _discard_delivery(turn)
        active = await self._runner.interrupt(
            session_id,
            self._session_agents.get(session_id),
            turn_id=running_turn_id,
        )
        cancelled_ids = [turn.id for turn in dropped]
        if active and running_turn_id:
            cancelled_ids.append(running_turn_id)
        return InterruptResult(bool(active or cancelled), tuple(cancelled_ids))

    async def cancel_queued(self, session_id: str, turn_id: str) -> bool:
        """Cancel one queued turn, leaving the running turn and the rest intact.

        Returns False when the turn has already started or is unknown. The
        drain and re-put below must not await: a submit can only land between
        awaits, and the worker relies on the queue staying consistent.
        """
        dropped: tuple[Turn, TurnStream] | None = None
        pending = self._pending.get(session_id)
        if (
            pending is not None
            and pending[0].id == turn_id
            and session_id not in self._started
        ):
            dropped = pending
            self._cancelled.add(turn_id)
        else:
            queue = self._queues.get(session_id)
            if queue is not None:
                kept: list[tuple[Turn, TurnStream]] = []
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    queue.task_done()
                    if dropped is None and item[0].id == turn_id:
                        dropped = item
                    else:
                        kept.append(item)
                for item in kept:
                    queue.put_nowait(item)
        if dropped is None:
            return False

        turn, stream = dropped
        stream.publish({"type": "turn_end", "status": "cancelled"})
        stream.close()
        if session_id not in self._running and session_id not in self._finishing:
            queue = self._queues.get(session_id)
            if queue is None or queue.empty():
                worker = self._workers.pop(session_id, None)
                if worker is not None:
                    worker.cancel()
                self._queues.pop(session_id, None)
                self._current.pop(session_id, None)
        await _discard_delivery(turn)
        return True

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
        for session_id, (_, stream) in list(self._pending.items()):
            if session_id not in self._started:
                stream.publish({"type": "turn_end", "status": "cancelled"})
                stream.close()
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
            self._pending.clear()
            self._current.clear()
            self._running.clear()
            self._finishing.clear()
            self._started.clear()
            self._cancelled.clear()
            self._session_agents.clear()
            self._deleting.clear()
            self._deleting_agents.clear()
            self._streams.clear()
            self._turn_sessions.clear()
