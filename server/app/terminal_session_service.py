import asyncio
import fcntl
import os
import pty
import select
import subprocess
import struct
import termios
from collections.abc import Awaitable, Callable
from pathlib import Path


class TerminalSessionService:
    def __init__(self, workspace: Path, working_directory: Path) -> None:
        self.sessions_dir = workspace / "terminal" / "sessions"
        self.working_directory = working_directory

    async def run_interactive_session(
        self,
        receive_message: Callable[[], Awaitable[dict[str, object]]],
        send_text: Callable[[str], Awaitable[None]],
    ) -> None:
        shell = self._shell_path()
        master_fd, slave_fd = pty.openpty()
        os.set_blocking(master_fd, False)
        process = subprocess.Popen(
            [shell],
            cwd=self.working_directory,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            env={**os.environ, "TERM": os.environ.get("TERM", "xterm-256color")},
        )
        os.close(slave_fd)

        async def read_pty() -> None:
            while True:
                data = await asyncio.to_thread(self._read_available, master_fd)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    await send_text(text)
                elif process.poll() is not None:
                    break
            status = process.poll()
            message = f"\r\n[terminal exited status={status}]\r\n"
            await send_text(message)

        async def write_pty() -> None:
            while process.poll() is None:
                message = await receive_message()
                message_type = message.get("type")
                if message_type == "input":
                    text = str(message.get("data", ""))
                    os.write(master_fd, text.encode("utf-8"))
                elif message_type == "resize":
                    cols = self._positive_int(message.get("cols"))
                    rows = self._positive_int(message.get("rows"))
                    if cols and rows:
                        self.resize_pty(master_fd, cols, rows)

        reader = asyncio.create_task(read_pty())
        writer = asyncio.create_task(write_pty())
        try:
            done, pending = await asyncio.wait(
                {reader, writer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2)
                except TimeoutError:
                    process.kill()
            os.close(master_fd)

    def resize_pty(self, fd: int, cols: int, rows: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _read_available(self, fd: int) -> bytes:
        readable, _, _ = select.select([fd], [], [], 0.1)
        if not readable:
            return b""
        try:
            return os.read(fd, 4096)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def _shell_path(self) -> str:
        for shell in (os.environ.get("SHELL"), "/bin/bash", "/bin/sh"):
            if shell and Path(shell).exists():
                return shell
        return "/bin/sh"

    def _positive_int(self, value: object) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return number
