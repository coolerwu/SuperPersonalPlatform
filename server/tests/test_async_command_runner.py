import asyncio
import sys

import pytest

from server.infrastructure.async_command_runner import AsyncCommandRunner


def test_async_command_runner_streams_stdout_and_stderr() -> None:
    async def scenario() -> None:
        runner = AsyncCommandRunner()
        events: list[tuple[str, str]] = []

        result = await runner.run(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            on_stdout=lambda text: events.append(("stdout", text)),
            on_stderr=lambda text: events.append(("stderr", text)),
        )

        assert result.returncode == 0
        assert "out" in result.stdout
        assert "err" in result.stderr
        assert ("stdout", "out\n") in events
        assert ("stderr", "err\n") in events

    asyncio.run(scenario())


def test_async_command_runner_timeout_terminates_process() -> None:
    async def scenario() -> None:
        runner = AsyncCommandRunner()

        with pytest.raises(asyncio.TimeoutError):
            await runner.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)

    asyncio.run(scenario())


def test_async_command_runner_cancel_terminates_process() -> None:
    async def scenario() -> None:
        runner = AsyncCommandRunner()
        handle = await runner.start([sys.executable, "-c", "import time; time.sleep(5)"])

        await handle.cancel()
        result = await handle.wait()

        assert result.returncode != 0

    asyncio.run(scenario())
