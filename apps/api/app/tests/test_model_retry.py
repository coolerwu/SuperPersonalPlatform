from __future__ import annotations

import pytest
from langchain_core.exceptions import ModelAuthenticationError

from app.services.model_retry import ModelRetryMixin, retry_delay


class _FlakyStreamBase:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def _astream(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("connection dropped")
        yield "a"
        yield "b"


class _FlakyStream(ModelRetryMixin, _FlakyStreamBase):
    retry_initial_delay = 0.0
    retry_backoff_factor = 0.0
    retry_max_delay = 0.0


class _PartialStreamBase:
    def __init__(self) -> None:
        self.calls = 0

    async def _astream(self, *_args, **_kwargs):
        self.calls += 1
        yield "partial"
        raise ConnectionError("dropped after output")


class _PartialStream(ModelRetryMixin, _PartialStreamBase):
    retry_initial_delay = 0.0
    retry_backoff_factor = 0.0
    retry_max_delay = 0.0


class _AuthStreamBase:
    def __init__(self) -> None:
        self.calls = 0

    async def _astream(self, *_args, **_kwargs):
        self.calls += 1
        raise ModelAuthenticationError("401")
        yield  # pragma: no cover


class _AuthStream(ModelRetryMixin, _AuthStreamBase):
    retry_initial_delay = 0.0
    retry_backoff_factor = 0.0
    retry_max_delay = 0.0


class _FlakyGenerateBase:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def _agenerate(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("connection dropped")
        return "ok"


class _FlakyGenerate(ModelRetryMixin, _FlakyGenerateBase):
    retry_initial_delay = 0.0
    retry_backoff_factor = 0.0
    retry_max_delay = 0.0


@pytest.mark.asyncio
async def test_stream_retries_when_no_tokens_were_emitted() -> None:
    model = _FlakyStream(failures=2)
    chunks = [chunk async for chunk in model._astream()]
    assert chunks == ["a", "b"]
    assert model.calls == 3


@pytest.mark.asyncio
async def test_stream_does_not_retry_after_partial_output() -> None:
    model = _PartialStream()
    chunks: list[str] = []
    with pytest.raises(ConnectionError):
        async for chunk in model._astream():
            chunks.append(chunk)
    assert chunks == ["partial"]
    assert model.calls == 1


@pytest.mark.asyncio
async def test_stream_does_not_retry_non_retryable_errors() -> None:
    model = _AuthStream()
    with pytest.raises(ModelAuthenticationError):
        async for _chunk in model._astream():
            pass
    assert model.calls == 1


@pytest.mark.asyncio
async def test_generate_retries_whole_calls() -> None:
    model = _FlakyGenerate(failures=2)
    assert await model._agenerate() == "ok"
    assert model.calls == 3


@pytest.mark.asyncio
async def test_generate_stops_after_max_attempts() -> None:
    model = _FlakyGenerate(failures=5)
    with pytest.raises(ConnectionError):
        await model._agenerate()
    assert model.calls == 4


def test_retry_delay_is_capped_and_jittered() -> None:
    delay = retry_delay(10, initial_delay=1.0, backoff_factor=2.0, max_delay=5.0)
    assert 3.75 <= delay <= 6.25
