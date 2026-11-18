"""Knowledge-base note discovery and cached index for the random notes page."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NOTE_SUFFIXES = frozenset({".md", ".txt"})
MAX_NOTE_BYTES = 2 * 1024 * 1024
TITLE_PREFIX_BYTES = 8192
INDEX_TTL_SECONDS = 60.0

_CLOSING_HASHES = re.compile(r"(?:^|\s+)#+\s*$")


@dataclass(frozen=True)
class NoteEntry:
    path: str  # POSIX path relative to the knowledge root
    title: str
    size: int


def _read_prefix(path: Path, limit: int = TITLE_PREFIX_BYTES) -> str:
    with path.open("rb") as handle:
        raw = handle.read(limit)
    return raw.decode("utf-8-sig", errors="replace")


def note_title(rel_path: str, text: str) -> str:
    lines = text.splitlines()
    start = 0
    if lines and lines[0] == "---":
        for index in range(1, len(lines)):
            if lines[index] == "---":
                start = index + 1
                break
    fence: str | None = None
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("```"):
            if fence is None:
                fence = "```"
            elif fence == "```":
                fence = None
            continue
        if stripped.startswith("~~~"):
            if fence is None:
                fence = "~~~"
            elif fence == "~~~":
                fence = None
            continue
        if fence is not None:
            continue
        if stripped.startswith("# "):
            title = _CLOSING_HASHES.sub("", stripped[2:].strip()).strip()
            if title:
                return title
    stem = Path(rel_path).stem.strip()
    return stem or Path(rel_path).name


def scan_notes(root: Path) -> dict[str, NoteEntry]:
    """Collect readable notes under root keyed by POSIX relative path."""
    entries: dict[str, NoteEntry] = {}
    if not root.is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            target = Path(dirpath) / name
            if target.suffix.lower() not in NOTE_SUFFIXES:
                continue
            try:
                if target.is_symlink() or not target.is_file():
                    continue
                size = target.stat().st_size
                if size > MAX_NOTE_BYTES:
                    continue
                text = _read_prefix(target)
            except OSError as exc:
                logger.warning("knowledge notes: cannot read %s: %s", target, exc)
                continue
            rel = target.relative_to(root).as_posix()
            entries[rel] = NoteEntry(path=rel, title=note_title(rel, text), size=size)
    return entries


_index_root: Path | None = None
_index_entries: dict[str, NoteEntry] = {}
_index_at = 0.0


def reset_index_cache() -> None:
    """Clear the module-level index cache (test hook)."""
    global _index_root, _index_entries, _index_at
    _index_root = None
    _index_entries = {}
    _index_at = 0.0


async def get_note_index(
    root: Path, *, ttl: float = INDEX_TTL_SECONDS
) -> dict[str, NoteEntry]:
    """Return the cached note index, rescanning in a worker thread when stale."""
    global _index_root, _index_entries, _index_at
    if not root.is_dir():
        # The mirror is briefly renamed aside during resets; never cache that.
        return {}
    if _index_root == root and time.monotonic() - _index_at < ttl:
        return dict(_index_entries)
    entries = await asyncio.to_thread(scan_notes, root)
    _index_root = root
    _index_entries = entries
    _index_at = time.monotonic()
    return dict(entries)
