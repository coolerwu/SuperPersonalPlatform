from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


StreamCallback = Callable[[str], None]


@dataclass(frozen=True)
class AsyncCommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class AsyncCommandHandle:
    def __init__(
        self,
        args: list[str],
        process: asyncio.subprocess.Process,
        on_stdout: StreamCallback | None = None,
        on_stderr: StreamCallback | None = None,
    ) -> None:
        self._args = args
        self._process = process
        self._stdout_chunks: list[str] = []
        self._stderr_chunks: list[str] = []
        self._stdout_task = asyncio.create_task(
            self._drain(process.stdout, self._stdout_chunks, on_stdout)
        )
        self._stderr_task = asyncio.create_task(
            self._drain(process.stderr, self._stderr_chunks, on_stderr)
        )
        self._result: AsyncCommandResult | None = None

    async def wait(self) -> AsyncCommandResult:
        if self._result is not None:
            return self._result
        returncode = await self._process.wait()
        await asyncio.gather(self._stdout_task, self._stderr_task)
        self._result = AsyncCommandResult(
            args=self._args,
            returncode=returncode,
            stdout="".join(self._stdout_chunks),
            stderr="".join(self._stderr_chunks),
        )
        return self._result

    async def cancel(self) -> None:
        if self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

    async def _drain(
        self,
        stream: asyncio.StreamReader | None,
        chunks: list[str],
        callback: StreamCallback | None,
    ) -> None:
        if stream is None:
            return
        while True:
            data = await stream.readline()
            if not data:
                break
            text = data.decode(errors="replace")
            chunks.append(text)
            if callback is not None:
                callback(text)


class AsyncCommandRunner:
    async def start(
        self,
        args: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        on_stdout: StreamCallback | None = None,
        on_stderr: StreamCallback | None = None,
    ) -> AsyncCommandHandle:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return AsyncCommandHandle(args, process, on_stdout=on_stdout, on_stderr=on_stderr)

    async def run(
        self,
        args: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        check: bool = False,
        on_stdout: StreamCallback | None = None,
        on_stderr: StreamCallback | None = None,
    ) -> AsyncCommandResult:
        handle = await self.start(
            args,
            cwd=cwd,
            env=env,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
        try:
            result = await asyncio.wait_for(handle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await handle.cancel()
            raise
        except asyncio.CancelledError:
            await handle.cancel()
            raise
        if check and result.returncode != 0:
            raise RuntimeError((result.stdout + result.stderr).strip() or f"command failed: {' '.join(args)}")
        return result
