"""Pure text helpers for channel replies and logs.

Extracted from the legacy ``channel_service`` module so the WeChat runtime can
split replies without importing the deprecated database layer. Keep this module
free of database, model and runtime-service imports.
"""

from __future__ import annotations

import re

from app.config import settings

DELIVERY_TRUNCATED_TEXT = "（内容过长，已截断）"
MAX_REPLY_CHUNKS = 10

_WHITESPACE_RE = re.compile(r"\s+")


def _split_blocks(text: str) -> list[str]:
    """Split on blank lines, but keep fenced code blocks together."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and not line.strip():
            block = "\n".join(current).strip("\n")
            if block.strip():
                blocks.append(block)
            current = []
    block = "\n".join(current).strip("\n")
    if block.strip():
        blocks.append(block)
    return blocks


def split_reply(text: str, max_chars: int | None = None) -> list[str]:
    """Split a final answer into WeChat-sized chunks at logical boundaries."""
    limit = max_chars or settings.channel_reply_max_chars
    if limit <= 0:
        return [text.strip() or "（无回复）"]
    content = (text or "").strip()
    if not content:
        return ["（无回复）"]
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for block in _split_blocks(content):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        flush()
        if len(block) <= limit:
            current = block
            continue
        for line in block.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    flush()

    if len(chunks) > MAX_REPLY_CHUNKS:
        chunks = chunks[:MAX_REPLY_CHUNKS]
        room = max(0, limit - len(DELIVERY_TRUNCATED_TEXT))
        chunks[-1] = chunks[-1][:room] + DELIVERY_TRUNCATED_TEXT
    return chunks or [content[:limit]]


def message_preview(text: str, limit: int = 40) -> str:
    """Single-line preview for logs (never includes tokens)."""
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]
