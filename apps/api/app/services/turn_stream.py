"""In-memory event stream for one turn.

One stream exists per turn. It is live-only: nothing is persisted, so a page
refresh loses the live view by design. The terminal event is
``{"type": "turn_end"}``, delivered exactly once to each current and future
subscriber. The runner may publish the terminal event itself with extra
details; ``close()`` then only closes the stream instead of appending a bare
duplicate. Events published before a subscriber attaches are buffered and
replayed on subscribe. Each event carries a buffer index so a client that lost
its connection can resume after the last event it saw. ``close()`` is
idempotent and ignores later publishes.
"""

from __future__ import annotations

import asyncio
from typing import Any

Event = tuple[int, dict[str, Any]]


class TurnStream:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the stream has been closed and will accept no more events."""
        return self._closed

    def _has_end(self) -> bool:
        return bool(self._buffer) and self._buffer[-1].get("type") == "turn_end"

    def publish(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        index = len(self._buffer)
        self._buffer.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait((index, event))

    async def subscribe(self, after: int = 0) -> asyncio.Queue[Event]:
        """Replay buffered events starting at index ``after`` (inclusive) and receive new ones.

        ``after`` is clamped to ``[0, len(buffer)]``; a value past the end waits for
        live events or, on a closed stream without a terminal in the buffer, yields the
        synthetic terminal.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        start = max(0, min(after, len(self._buffer)))
        for index in range(start, len(self._buffer)):
            queue.put_nowait((index, self._buffer[index]))
        if self._closed:
            if not self._has_end():
                queue.put_nowait((len(self._buffer), {"type": "turn_end"}))
            return queue
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Detach a subscriber. Idempotent and safe after ``close()``."""
        self._subscribers.discard(queue)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._has_end():
            index = len(self._buffer)
            for queue in list(self._subscribers):
                queue.put_nowait((index, {"type": "turn_end"}))
        self._subscribers.clear()
