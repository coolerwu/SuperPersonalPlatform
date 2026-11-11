from __future__ import annotations

import asyncio
import os
import signal


def kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill a process started with ``start_new_session=True`` and its children."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
