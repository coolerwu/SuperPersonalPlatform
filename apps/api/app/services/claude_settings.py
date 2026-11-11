"""Write platform configuration into Claude Code's own settings.

The platform does not run a transcript-retention job. Claude Code sweeps
transcripts under ``CLAUDE_CONFIG_DIR`` according to its ``cleanupPeriodDays``
setting; the platform only keeps that value in sync with
``runtime.session_retention_days`` in ``claude/settings.json`` — the same file
the plugin CLI mutates, so every CLI invocation sees the same policy.

The lock here is shared with the plugin CLI mutations: both write that file.

The runner must keep the ``user`` setting source enabled for this value to be
loaded; ``docs/intent.md`` F3 and the runner options both rely on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from app.services.claude_env import claude_paths

logger = logging.getLogger(__name__)

# Claude Code requires at least one day ("must be at least 1"). A platform
# retention of 0 means "never clean", so it is written as a century instead.
CLEANUP_DISABLED_DAYS = 36500

# A plugin CLI mutation may hold the shared lock for minutes (network install);
# a settings save should fail fast instead of hanging behind it.
LOCK_WAIT_SECONDS = 10.0

_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


class ClaudeSettingsError(RuntimeError):
    """The runtime Claude settings file could not be read or written."""


def config_lock() -> asyncio.Lock:
    """Lock shared by every platform writer of ``claude/settings.json``.

    Rebuilt when the running loop changes so tests can use fresh event loops.
    """
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


def settings_path(home: Path | None = None) -> Path:
    return claude_paths(home)["config"] / "settings.json"


def cleanup_period_days(retention_days: int) -> int:
    """Map the platform retention (0 = keep) to Claude's positive day count."""
    return CLEANUP_DISABLED_DAYS if retention_days <= 0 else retention_days


def client_ttl_exceeds_retention(ttl_seconds: float, retention_days: int) -> bool:
    """Whether a cached client could outlive Claude's transcript sweep.

    A cached client keeps a session in memory; if it could stay idle longer
    than the retention window, Claude might delete its transcript while the
    client still owns the conversation and the next turn would recreate it.
    """
    return retention_days > 0 and ttl_seconds >= retention_days * 86400


def _load(path: Path) -> dict:
    try:
        raw = path.read_text("utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ClaudeSettingsError(f"无法读取 {path}：{exc}") from exc
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ClaudeSettingsError(f"{path} 不是有效的 JSON") from exc
    if not isinstance(data, dict):
        raise ClaudeSettingsError(f"{path} 的顶层不是对象")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=".settings-", suffix=".json"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise ClaudeSettingsError(f"无法写入 {path}：{exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)


async def sync_cleanup_period(
    retention_days: int, *, home: Path | None = None
) -> bool:
    """Write ``cleanupPeriodDays`` into the runtime Claude settings.

    Returns True when the file changed. An existing file that cannot be parsed
    or written raises instead of being replaced, so plugin state is not lost.
    """
    target = cleanup_period_days(retention_days)
    path = settings_path(home)
    lock = config_lock()
    try:
        async with asyncio.timeout(LOCK_WAIT_SECONDS):
            await lock.acquire()
    except TimeoutError as exc:
        raise ClaudeSettingsError(
            "另一个 Claude 设置写入正在进行，请稍后再试"
        ) from exc
    try:
        data = _load(path)
        current = data.get("cleanupPeriodDays")
        # A hand-edited boolean must not compare equal to 1.
        if (
            isinstance(current, int)
            and not isinstance(current, bool)
            and current == target
        ):
            return False
        data["cleanupPeriodDays"] = target
        # Deliberately synchronous: the read-modify-write must not be
        # interrupted by cancellation while another writer waits for the lock.
        _write(path, data)
        logger.info("synced cleanupPeriodDays=%d to %s", target, path)
        return True
    finally:
        lock.release()
