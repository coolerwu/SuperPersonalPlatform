"""In-memory event stream for one turn.

One stream exists per turn. It is live-only: nothing is persisted, so a page
refresh loses the live view by design. The terminal event is
``{"type": "turn_end"}``, delivered exactly once to each current and future
subscriber. The runner may publish the terminal event itself with extra
details; ``close()`` then only closes the stream instead of appending a bare
duplicate. Events published before a subscriber attaches are buffered and
replayed on subscribe; ``close()`` is idempotent and ignores later publishes.
"""

from __future__ import annotations

import asyncio
from typing import Any


class TurnStream:
    def __init__(self) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
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
        self._buffer.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self._buffer:
            queue.put_nowait(event)
        if self._closed:
            if not self._has_end():
                queue.put_nowait({"type": "turn_end"})
            return queue
        self._subscribers.add(queue)
        return queue

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._has_end():
            for queue in list(self._subscribers):
                queue.put_nowait({"type": "turn_end"})
        self._subscribers.clear()
