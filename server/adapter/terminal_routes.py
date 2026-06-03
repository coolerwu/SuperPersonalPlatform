import asyncio
import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from server.adapter.auth_routes import SESSION_COOKIE, is_authenticated_request
from server.adapter.dependencies import AppContainer


class InvalidTerminalMessageError(Exception):
    pass


class TerminalAuthenticationError(Exception):
    pass


def create_terminal_router(container: AppContainer, working_directory: Path | None = None) -> APIRouter:
    router = APIRouter(
        prefix="/api/system/terminal",
        tags=["terminal"],
    )
    terminal_working_directory = working_directory or Path(__file__).resolve().parents[2]

    @router.websocket("/connect")
    async def connect_terminal(websocket: WebSocket) -> None:
        session_cookie = websocket.cookies.get(SESSION_COOKIE)
        if not _verify_current_session(container, session_cookie):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        async def receive_terminal_message() -> dict[str, object]:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                raise InvalidTerminalMessageError("terminal message must be an object")
            if not _verify_current_session(container, session_cookie):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                raise TerminalAuthenticationError("terminal session is no longer authenticated")
            return data

        async def send_terminal_output(text: str) -> None:
            await websocket.send_json({"type": "output", "data": text})

        try:
            await run_interactive_terminal(
                receive_terminal_message,
                send_terminal_output,
                terminal_working_directory,
            )
        except WebSocketDisconnect:
            return
        except TerminalAuthenticationError:
            return
        except InvalidTerminalMessageError:
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)

    return router


async def run_interactive_terminal(
    receive_message: Callable[[], Awaitable[dict[str, object]]],
    send_text: Callable[[str], Awaitable[None]],
    working_directory: Path,
) -> None:
    shell = _shell_path()
    master_fd, slave_fd = pty.openpty()
    os.set_blocking(master_fd, False)
    process = subprocess.Popen(
        [shell],
        cwd=working_directory,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
        env={**os.environ, "TERM": os.environ.get("TERM", "xterm-256color")},
    )
    os.close(slave_fd)

    async def read_pty() -> None:
        while True:
            data = await asyncio.to_thread(_read_available, master_fd)
            if data:
                text = data.decode("utf-8", errors="replace")
                await send_text(text)
            elif process.poll() is not None:
                break
        status_code = process.poll()
        message = f"\r\n[terminal exited status={status_code}]\r\n"
        await send_text(message)

    async def write_pty() -> None:
        while process.poll() is None:
            message = await receive_message()
            message_type = message.get("type")
            if message_type == "input":
                text = str(message.get("data", ""))
                os.write(master_fd, text.encode("utf-8"))
            elif message_type == "resize":
                cols = _positive_int(message.get("cols"))
                rows = _positive_int(message.get("rows"))
                if cols and rows:
                    resize_pty(master_fd, cols, rows)

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


def resize_pty(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _read_available(fd: int) -> bytes:
    readable, _, _ = select.select([fd], [], [], 0.1)
    if not readable:
        return b""
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return b""
    except OSError:
        return b""


def _shell_path() -> str:
    for shell in (os.environ.get("SHELL"), "/bin/bash", "/bin/sh"):
        if shell and Path(shell).exists():
            return shell
    return "/bin/sh"


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _verify_current_session(container: AppContainer, session_cookie: str | None) -> bool:
    return is_authenticated_request(container, session_cookie)
