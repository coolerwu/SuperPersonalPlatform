from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NOTE_SUFFIXES = frozenset({".md", ".txt"})
MAX_NOTE_BYTES = 2 * 1024 * 1024
TITLE_PREFIX_BYTES = 8192
INDEX_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class NoteEntry:
    path: str  # POSIX path relative to the knowledge root
    title: str
    size: int


def _read_prefix(path: Path, limit: int = TITLE_PREFIX_BYTES) -> str:
    with path.open("rb") as handle:
        raw = handle.read(limit)
    return raw.decode("utf-8", errors="replace")


def note_title(rel_path: str, text: str) -> str:
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                return title
    stem = Path(rel_path).stem.strip()
    return stem or Path(rel_path).name


def scan_notes(root: Path) -> dict[str, NoteEntry]:
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


_index_lock = asyncio.Lock()
_index_root: Path | None = None
_index_entries: dict[str, NoteEntry] = {}
_index_at = 0.0


def reset_index_cache() -> None:
    global _index_root, _index_entries, _index_at
    _index_root = None
    _index_entries = {}
    _index_at = 0.0


async def get_note_index(
    root: Path, *, ttl: float = INDEX_TTL_SECONDS
) -> dict[str, NoteEntry]:
    global _index_root, _index_entries, _index_at
    now = time.monotonic()
    async with _index_lock:
        if _index_root == root and now - _index_at < ttl:
            return dict(_index_entries)
        entries = scan_notes(root)
        _index_root = root
        _index_entries = entries
        _index_at = now
        return dict(entries)
