from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress


class DebouncedTaskExecutor:
    def __init__(self, callback: Callable[[str], Awaitable[None]]) -> None:
        self._callback = callback
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def schedule(self, key: str, *, delay_seconds: float) -> None:
        await self.cancel(key)
        self._tasks[key] = asyncio.create_task(self._run_after_delay(key, delay_seconds))

    async def flush(self, key: str) -> None:
        await self.cancel(key)
        await self._callback(key)

    async def cancel(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def cancel_all(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _run_after_delay(self, key: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(max(float(delay_seconds), 0.0))
            await self._callback(key)
        except asyncio.CancelledError:
            raise
        finally:
            task = self._tasks.get(key)
            if task is asyncio.current_task():
                self._tasks.pop(key, None)
