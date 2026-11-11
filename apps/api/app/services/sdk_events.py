"""Translate Claude Agent SDK stream and result messages into platform events.

This module only translates. Run lifecycle, persistence and cancellation
handling stay in the runner; the mapper is a pure, stateful decoder for one
turn's raw Anthropic SSE events.

Tool activity rows require ``include_partial_messages=True`` on the SDK
options (the runner enables it). Without partial messages there are no
``StreamEvent`` objects, so real-time text, thinking and tool rows are lost;
the final result text and terminal outcome still come from ``ResultMessage``.
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


TASK_SUBTYPES = frozenset(
    {"task_started", "task_progress", "task_updated", "task_notification"}
)


def origin_kind(message: Any) -> str | None:
    """Read ``origin.kind`` from a user/result message, dict or object."""
    origin = getattr(message, "origin", None)
    if isinstance(origin, dict):
        value = origin.get("kind")
    else:
        value = getattr(origin, "kind", None)
    return str(value) if value else None


def _field(message: Any, name: str) -> Any:
    value = getattr(message, name, None)
    if value is None:
        data = getattr(message, "data", None)
        if isinstance(data, dict):
            value = data.get(name)
    return value


def _usage(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def task_event(message: Any) -> dict[str, Any] | None:
    """Map one task lifecycle SystemMessage to a platform event."""
    subtype = str(getattr(message, "subtype", "") or "")
    if subtype not in TASK_SUBTYPES:
        return None
    task_id = str(_field(message, "task_id") or "")
    if not task_id:
        return None
    tool_use_id = _field(message, "tool_use_id") or None
    if subtype == "task_started":
        return {
            "type": "task_started",
            "task_id": task_id,
            "tool_use_id": tool_use_id,
            "description": str(_field(message, "description") or ""),
            "task_type": _field(message, "task_type") or None,
        }
    if subtype == "task_progress":
        return {
            "type": "task_progress",
            "task_id": task_id,
            "tool_use_id": tool_use_id,
            "description": str(_field(message, "description") or ""),
            "last_tool_name": _field(message, "last_tool_name") or None,
            "usage": _usage(_field(message, "usage")),
        }
    if subtype == "task_updated":
        patch = getattr(message, "patch", None)
        status = getattr(message, "status", None)
        if status is None and isinstance(patch, dict):
            status = patch.get("status")
        return {
            "type": "task_updated",
            "task_id": task_id,
            "status": str(status) if status else None,
        }
    return {
        "type": "task_notification",
        "task_id": task_id,
        "tool_use_id": tool_use_id,
        "status": str(_field(message, "status") or ""),
        "summary": str(_field(message, "summary") or ""),
        "usage": _usage(_field(message, "usage")),
    }


def _block_field(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def subagent_message_events(message: Any) -> list[dict[str, Any]]:
    """Map a subagent AssistantMessage's content blocks to platform events.

    Subagent thinking is intentionally ignored; text and tool calls are enough
    to follow what a child agent is doing without flooding the live trace.
    """
    if type(message).__name__ != "AssistantMessage":
        return []
    parent = getattr(message, "parent_tool_use_id", None)
    content = getattr(message, "content", None)
    if not parent or not isinstance(content, list):
        return []
    events: list[dict[str, Any]] = []
    for block in content:
        kind = _block_field(block, "type")
        if kind == "text":
            text = str(_block_field(block, "text") or "")
            if text.strip():
                events.append(
                    {
                        "type": "subagent_text",
                        "parent_tool_use_id": parent,
                        "text": text,
                    }
                )
        elif kind == "tool_use":
            tool_input = _block_field(block, "input")
            events.append(
                {
                    "type": "subagent_tool_call",
                    "parent_tool_use_id": parent,
                    "id": str(_block_field(block, "id") or ""),
                    "name": str(_block_field(block, "name") or ""),
                    "input": tool_input if isinstance(tool_input, dict) else {},
                }
            )
    return events


class StreamMapper:
    def __init__(self) -> None:
        self._tool_id = ""
        self._tool_name: str | None = None
        self._tool_json = ""

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._tool_id = str(block.get("id") or "")
                self._tool_name = str(block.get("name") or "")
                self._tool_json = ""
            else:
                # A non-tool block means any dangling tool capture is stale.
                self._tool_id = ""
                self._tool_name = None
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
                if self._tool_name is not None:
                    self._tool_json += str(delta.get("partial_json") or "")
            return []
        if kind == "content_block_stop" and self._tool_name is not None:
            tool_id, name, raw = self._tool_id, self._tool_name, self._tool_json
            self._tool_id, self._tool_name, self._tool_json = "", None, ""
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            return [
                {
                    "type": "tool_call",
                    "id": tool_id,
                    "name": name,
                    "input": parsed,
                }
            ]
        return []


def outcome_from_result(message: Any, *, fallback_text: str) -> TurnOutcome:
    subtype = str(getattr(message, "subtype", "") or "")
    terminal = getattr(message, "terminal_reason", None)
    result = getattr(message, "result", None)
    errors = getattr(message, "errors", None) or []
    usage = getattr(message, "usage", None)
    is_error = bool(getattr(message, "is_error", False))
    api_error_status = getattr(message, "api_error_status", None)
    if terminal in ("aborted_streaming", "aborted_tools"):
        return TurnOutcome(
            status="cancelled",
            output=fallback_text,
            terminal_reason=terminal,
            usage=usage,
        )
    if subtype == "success" and terminal in (None, "completed") and not is_error:
        return TurnOutcome(
            status="completed",
            output=str(result or fallback_text),
            terminal_reason=terminal,
            usage=usage,
        )
    error_text = "; ".join(str(item) for item in errors if item)
    if not error_text:
        result_text = str(result).strip() if result is not None else ""
        if result_text:
            error_text = result_text
        elif terminal:
            error_text = str(terminal)
        elif api_error_status is not None:
            error_text = f"API error (HTTP {api_error_status})"
        else:
            error_text = "run failed"
    return TurnOutcome(
        status="failed",
        output=fallback_text,
        error=error_text,
        terminal_reason=terminal,
        usage=usage,
    )
