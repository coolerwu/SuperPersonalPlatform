"""Shared validation for ids that become filesystem path components.

Agent ids name a Claude SDK project directory and session ids name a transcript
file inside it, so both are validated before being spliced into a path.
"""

from __future__ import annotations

import re
import uuid

AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


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
    return isinstance(value, str) and AGENT_ID_RE.fullmatch(value) is not None
