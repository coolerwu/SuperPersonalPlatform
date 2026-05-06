import asyncio
import fcntl
import json
import os
import pty
import select
import subprocess
import struct
import termios
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class InvalidTerminalSessionError(Exception):
    pass


@dataclass(frozen=True)
class TerminalSessionSummary:
    name: str
    path: str
    size: int
    modified_at: str


@dataclass(frozen=True)
class TerminalSessionContent:
    name: str
    path: str
    size: int
    modified_at: str
    content: str


class TerminalSessionService:
    def __init__(self, workspace: Path, working_directory: Path) -> None:
        self.sessions_dir = workspace / "terminal" / "sessions"
        self.working_directory = working_directory

    def list_sessions(self) -> list[TerminalSessionSummary]:
        self._ensure_sessions_dir()
        files = sorted(
            self.sessions_dir.glob("terminal-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [self._summary(path) for path in files if path.is_file()]

    def read_session(self, name: str) -> TerminalSessionContent:
        self._ensure_sessions_dir()
        path = self._resolve_session_path(name)
        if not path.exists():
            raise FileNotFoundError(name)
        if not path.is_file():
            raise InvalidTerminalSessionError("terminal session target is not a file")

        summary = self._summary(path)
        return TerminalSessionContent(
            name=summary.name,
            path=summary.path,
            size=summary.size,
            modified_at=summary.modified_at,
            content=path.read_text(encoding="utf-8"),
        )

    def delete_session(self, name: str) -> None:
        self._ensure_sessions_dir()
        path = self._resolve_session_path(name)
        if not path.exists():
            raise FileNotFoundError(name)
        if not path.is_file():
            raise InvalidTerminalSessionError("terminal session target is not a file")
        path.unlink()

    async def run_interactive_session(
        self,
        receive_message: Callable[[], Awaitable[dict[str, object]]],
        send_text: Callable[[str], Awaitable[None]],
    ) -> Path:
        self._ensure_sessions_dir()
        session_path = self._new_session_path()
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
        self._append_event(
            session_path,
            "system",
            f"started shell={shell} cwd={self.working_directory}",
        )

        async def read_pty() -> None:
            while True:
                data = await asyncio.to_thread(self._read_available, master_fd)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    self._append_event(session_path, "output", text)
                    await send_text(text)
                elif process.poll() is not None:
                    break
            status = process.poll()
            message = f"\r\n[terminal exited status={status}]\r\n"
            self._append_event(session_path, "system", f"exited status={status}")
            await send_text(message)

        async def write_pty() -> None:
            while process.poll() is None:
                message = await receive_message()
                message_type = message.get("type")
                if message_type == "input":
                    text = str(message.get("data", ""))
                    self._append_event(session_path, "input", text)
                    os.write(master_fd, text.encode("utf-8"))
                elif message_type == "resize":
                    cols = self._positive_int(message.get("cols"))
                    rows = self._positive_int(message.get("rows"))
                    if cols and rows:
                        self.resize_pty(master_fd, cols, rows)
                        self._append_event(
                            session_path,
                            "system",
                            f"resize cols={cols} rows={rows}",
                        )

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
            self._append_event(session_path, "system", f"closed status={process.poll()}")
            os.close(master_fd)

        return session_path

    def _new_session_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return self.sessions_dir / f"terminal-{timestamp}-{suffix}.jsonl"

    def _ensure_sessions_dir(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_session_path(self, name: str) -> Path:
        if (
            Path(name).name != name
            or not name.startswith("terminal-")
            or not name.endswith(".jsonl")
        ):
            raise InvalidTerminalSessionError("invalid terminal session name")
        return self.sessions_dir / name

    def _summary(self, path: Path) -> TerminalSessionSummary:
        stat = path.stat()
        return TerminalSessionSummary(
            name=path.name,
            path=str(path),
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        )

    def _append_event(self, path: Path, stream: str, content: str) -> None:
        event = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "stream": stream,
            "content": content,
        }
        with path.open("a", encoding="utf-8") as session_file:
            session_file.write(json.dumps(event, ensure_ascii=False) + "\n")

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
