"""Random knowledge notes: discovery and TTL index, shuffle-deck state, and draw orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

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


CYCLE_RESET_NOTICE = "本轮已看完，重新洗牌"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeckState:
    cycle_started_at: str = ""
    seen: set[str] = field(default_factory=set)


def load_deck_state(path: Path) -> DeckState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DeckState(cycle_started_at=_utcnow())
    except (OSError, ValueError):
        logger.warning("knowledge notes: resetting unreadable deck state %s", path)
        return DeckState(cycle_started_at=_utcnow())
    if not isinstance(payload, dict):
        logger.warning("knowledge notes: resetting malformed deck state %s", path)
        return DeckState(cycle_started_at=_utcnow())
    seen_raw = payload.get("seen")
    if isinstance(seen_raw, list):
        strings = [item for item in seen_raw if isinstance(item, str)]
        seen = set(strings)
        if len(strings) != len(seen_raw):
            logger.warning(
                "knowledge notes: dropping non-string seen entries in deck state %s",
                path,
            )
    else:
        if "seen" in payload:
            logger.warning(
                "knowledge notes: ignoring non-list seen in deck state %s", path
            )
        seen = set()
    cycle = payload.get("cycle_started_at")
    cycle_ok = isinstance(cycle, str) and bool(cycle)
    if "cycle_started_at" in payload and not cycle_ok:
        logger.warning(
            "knowledge notes: replacing unusable cycle_started_at in deck state %s", path
        )
    return DeckState(
        cycle_started_at=cycle if cycle_ok else _utcnow(),
        seen=seen,
    )


def save_deck_state(path: Path, state: DeckState) -> None:
    payload = json.dumps(
        {"cycle_started_at": state.cycle_started_at, "seen": sorted(state.seen)},
        ensure_ascii=False,
        indent=2,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        logger.warning("knowledge notes: cannot save deck state %s: %s", path, exc)


def candidate_order(
    entries: dict[str, NoteEntry],
    state: DeckState,
    exclude: str | None,
    rng: random.Random | None = None,
) -> tuple[list[str], bool]:
    """Ordered candidates; second value is True when a new cycle starts."""
    picker = rng or random.Random()
    known = set(entries)
    unseen = sorted(known - (state.seen & known))
    if unseen:
        picker.shuffle(unseen)
        candidates, reset = unseen, False
    else:
        candidates = sorted(known)
        picker.shuffle(candidates)
        reset = True
    if exclude in candidates and len(candidates) > 1:
        candidates.remove(exclude)
        candidates.append(exclude)
    return candidates, reset


class KnowledgeNotesEmpty(Exception):
    """No readable notes under the knowledge directory."""


class NoteReadError(Exception):
    """A note could not be read safely."""


def read_note_text(root: Path, rel_path: str) -> str:
    try:
        root_resolved = root.resolve()
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root_resolved):
            raise NoteReadError(f"path escapes knowledge dir: {rel_path}")
        if not target.is_file():
            raise NoteReadError(f"note is not a readable file: {rel_path}")
        if target.stat().st_size > MAX_NOTE_BYTES:
            raise NoteReadError(f"note exceeds size limit: {rel_path}")
        return target.read_text(encoding="utf-8-sig", errors="replace")
    except NoteReadError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise NoteReadError(f"cannot read note: {rel_path}") from exc


_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _deck_lock() -> asyncio.Lock:
    """Deck lock for the current event loop (fresh loops in tests rebuild it)."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[loop] = lock
    return lock


async def draw_random_note(
    exclude: str | None,
) -> tuple[NoteEntry, str, dict[str, object]]:
    root = settings.knowledge_dir
    entries = await get_note_index(root)
    if not entries:
        raise KnowledgeNotesEmpty()
    state_path = settings.knowledge_deck_path
    async with _deck_lock():
        state = load_deck_state(state_path)
        candidates, cycle_reset = candidate_order(entries, state, exclude)
        last_error: Exception | None = None
        for rel in candidates:
            try:
                content = await asyncio.to_thread(read_note_text, root, rel)
            except NoteReadError as exc:
                last_error = exc
                logger.warning("knowledge notes: skipping unreadable note %s", rel)
                continue
            if cycle_reset:
                state = DeckState(cycle_started_at=_utcnow(), seen={rel})
            else:
                state.seen.add(rel)
            known = set(entries)
            state.seen &= known
            save_deck_state(state_path, state)
            meta: dict[str, object] = {
                "seen": len(state.seen & known),
                "total": len(known),
                "notice": CYCLE_RESET_NOTICE if cycle_reset else "",
            }
            return entries[rel], content, meta
        raise NoteReadError("读取笔记失败") from last_error
