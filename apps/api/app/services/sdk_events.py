"""Translate Claude Agent SDK stream and result messages into platform events.

This module only translates. Run lifecycle, persistence and cancellation
handling stay in the runner; the mapper is a pure, stateful decoder for one
turn's raw Anthropic SSE events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class TurnOutcome:
    status: str
    output: str = ""
    error: str | None = None
    terminal_reason: str | None = None
    usage: dict[str, Any] | None = None


class StreamMapper:
    def __init__(self) -> None:
        self._tool_name: str | None = None
        self._tool_json = ""

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._tool_name = str(block.get("name") or "")
                self._tool_json = ""
            return []
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = str(delta.get("text") or "")
                return [{"type": "assistant_delta", "delta": text}] if text else []
            if delta.get("type") == "thinking_delta":
                thinking = str(delta.get("thinking") or "")
                if thinking:
                    return [{"type": "reasoning_delta", "delta": thinking}]
                return []
            if delta.get("type") == "input_json_delta":
                self._tool_json += str(delta.get("partial_json") or "")
            return []
        if kind == "content_block_stop" and self._tool_name:
            name, raw = self._tool_name, self._tool_json
            self._tool_name, self._tool_json = None, ""
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {}
            return [{"type": "tool_call", "name": name, "input": parsed}]
        return []


def outcome_from_result(message: Any, *, fallback_text: str) -> TurnOutcome:
    subtype = str(getattr(message, "subtype", "") or "")
    terminal = getattr(message, "terminal_reason", None)
    result = getattr(message, "result", None)
    errors = getattr(message, "errors", None) or []
    usage = getattr(message, "usage", None)
    if terminal in ("aborted_streaming", "aborted_tools"):
        return TurnOutcome(
            status="cancelled",
            output=fallback_text,
            terminal_reason=terminal,
            usage=usage,
        )
    if subtype == "success" and terminal in (None, "completed"):
        return TurnOutcome(
            status="completed",
            output=str(result or fallback_text),
            terminal_reason=terminal,
            usage=usage,
        )
    error_text = "; ".join(str(item) for item in errors if item) or str(
        result or terminal or "run failed"
    )
    return TurnOutcome(
        status="failed",
        output=fallback_text,
        error=error_text,
        terminal_reason=terminal,
        usage=usage,
    )
