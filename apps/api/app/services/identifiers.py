"""Shared validation for ids that become filesystem path components.

Agent ids name a Claude SDK project directory and session ids name a transcript
file inside it, so both are validated before being spliced into a path.
"""

from __future__ import annotations

import re
import uuid

AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# Windows reserves these device names in every directory, case-insensitively.
# The Claude CLI falls back to the project directory name when it rejects an
# agent id, which would break transcript-path assumptions.
_RESERVED_AGENT_IDS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def is_canonical_uuid(value: object) -> bool:
    """Return True only for a canonical, lowercase-hyphenated UUID string."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return str(parsed) == value


def valid_agent_id(value: object) -> bool:
    """Return True only for an agent id safe to use as a path component."""
    if not isinstance(value, str) or AGENT_ID_RE.fullmatch(value) is None:
        return False
    return value.lower() not in _RESERVED_AGENT_IDS
