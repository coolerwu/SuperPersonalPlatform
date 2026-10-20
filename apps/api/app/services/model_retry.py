from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, ClassVar

from langchain_core.exceptions import ModelError

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_DELAY = 30.0


def log_retryable(exc: BaseException, *, agent_name: str, session_id: str) -> bool:
    if not isinstance(exc, Exception):
        return False
    retryable = exc.is_retryable if isinstance(exc, ModelError) else True
    if retryable:
        logger.warning(
            "model call failed (retryable) agent=%s session=%s error=%s: %s",
            agent_name,
            session_id,
            type(exc).__name__,
            exc,
        )
    return retryable


def retry_delay(
    attempt: int,
    *,
    initial_delay: float,
    backoff_factor: float,
    max_delay: float,
) -> float:
    base = min(initial_delay * (backoff_factor**attempt), max_delay)
    return max(0.0, base * random.uniform(0.75, 1.25))


class ModelRetryMixin:
    """Retry model calls on the model itself so every agent inherits it.

    deepagents only passes same-named default middleware to the built-in
    general-purpose subagent, so middleware-based retry never reaches it.
    Passing a model class that retries fixes both the main and subagents.

    ``_astream`` retries only when the failed attempt emitted no chunks:
    replaying a partially streamed answer would duplicate text in the UI.
    ``_agenerate`` has nothing buffered, so it can retry the whole call.
    """

    retry_max_attempts: ClassVar[int] = DEFAULT_MAX_RETRIES
    retry_initial_delay: ClassVar[float] = DEFAULT_INITIAL_DELAY
    retry_backoff_factor: ClassVar[float] = DEFAULT_BACKOFF_FACTOR
    retry_max_delay: ClassVar[float] = DEFAULT_MAX_DELAY
    retry_agent_name: ClassVar[str] = ""
    retry_session_id: ClassVar[str] = ""

    def _should_retry(self, exc: Exception, attempt: int) -> bool:
        if not log_retryable(
            exc,
            agent_name=self.retry_agent_name,
            session_id=self.retry_session_id,
        ):
            return False
        return attempt < self.retry_max_attempts

    async def _retry_sleep(self, attempt: int) -> None:
        delay = retry_delay(
            attempt,
            initial_delay=self.retry_initial_delay,
            backoff_factor=self.retry_backoff_factor,
            max_delay=self.retry_max_delay,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    async def _astream(self, *args: Any, **kwargs: Any):
        emitted = False
        for attempt in range(self.retry_max_attempts + 1):
            try:
                async for chunk in super()._astream(*args, **kwargs):
                    emitted = True
                    yield chunk
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if emitted:
                    logger.warning(
                        "model stream failed after partial output; not retrying "
                        "agent=%s session=%s error=%s: %s",
                        self.retry_agent_name,
                        self.retry_session_id,
                        type(exc).__name__,
                        exc,
                    )
                    raise
                if not self._should_retry(exc, attempt):
                    raise
                await self._retry_sleep(attempt)

    async def _agenerate(self, *args: Any, **kwargs: Any):
        for attempt in range(self.retry_max_attempts + 1):
            try:
                return await super()._agenerate(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._should_retry(exc, attempt):
                    raise
                await self._retry_sleep(attempt)
        raise RuntimeError("model retry loop exited without a result")
