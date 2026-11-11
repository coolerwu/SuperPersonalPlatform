from types import SimpleNamespace

from app.services.sdk_events import (
    StreamMapper,
    origin_kind,
    outcome_from_result,
    subagent_message_events,
    task_event,
)


class AssistantMessage:
    def __init__(self, content, parent_tool_use_id=None):
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id


def test_text_and_thinking_deltas():
    mapper = StreamMapper()
    assert mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hi"},
        }
    ) == [{"type": "assistant_delta", "delta": "hi"}]
    assert mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hm"},
        }
    ) == [{"type": "reasoning_delta", "delta": "hm"}]


def test_tool_call_accumulates_input():
    mapper = StreamMapper()
    assert mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    ) == []
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '"/tmp/x"}'},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/x"}}
    ]


def test_malformed_tool_json_falls_back_to_empty_input():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "Read", "input": {}}
    ]


def test_non_dict_tool_json_falls_back_to_empty_input():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": "null"},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "Read", "input": {}}
    ]


def test_consecutive_tool_uses_emit_separate_calls():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "First", "id": "t1", "input": {}},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"a": 1}'},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "First", "input": {"a": 1}}
    ]
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Second", "id": "t2", "input": {}},
        }
    )
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"b": 2}'},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t2", "name": "Second", "input": {"b": 2}}
    ]


def test_empty_text_and_thinking_deltas_produce_no_events():
    mapper = StreamMapper()
    assert mapper.feed(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}}
    ) == []
    assert mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": ""},
        }
    ) == []


def test_non_tool_block_stop_emits_nothing():
    mapper = StreamMapper()
    assert mapper.feed({"type": "content_block_stop"}) == []


def test_non_tool_start_clears_dangling_tool_capture():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    )
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == []


def test_input_json_delta_without_capture_is_ignored():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"leak": true}'},
        }
    )
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "Read", "input": {}}
    ]


def test_tool_with_empty_name_still_emits_call():
    mapper = StreamMapper()
    mapper.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "", "id": "t1", "input": {}},
        }
    )
    assert mapper.feed({"type": "content_block_stop"}) == [
        {"type": "tool_call", "id": "t1", "name": "", "input": {}}
    ]


def test_outcome_completed():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason="completed",
        result="done",
        usage={"input_tokens": 1, "output_tokens": 2},
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="")
    assert outcome.status == "completed"
    assert outcome.output == "done"


def test_success_without_terminal_reason_completes():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason=None,
        result="done",
        usage=None,
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="fallback")
    assert outcome.status == "completed"
    assert outcome.output == "done"


def test_completed_with_empty_result_uses_fallback_text():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason="completed",
        result="",
        usage=None,
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="fallback")
    assert outcome.status == "completed"
    assert outcome.output == "fallback"


def test_usage_is_passed_through():
    usage = {"input_tokens": 3, "output_tokens": 4}
    message = SimpleNamespace(
        subtype="success",
        terminal_reason="completed",
        result="done",
        usage=usage,
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="")
    assert outcome.usage is usage


def test_cancelled_uses_fallback_text():
    usage = {"input_tokens": 3, "output_tokens": 4}
    message = SimpleNamespace(
        subtype="success",
        terminal_reason="aborted_tools",
        result="ignored",
        usage=usage,
        errors=None,
        api_error_status=None,
    )
    outcome = outcome_from_result(message, fallback_text="partial")
    assert outcome.status == "cancelled"
    assert outcome.output == "partial"


def test_api_error_status_is_used_in_error_text():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason=None,
        result=None,
        usage=None,
        errors=None,
        api_error_status=429,
        is_error=True,
    )
    outcome = outcome_from_result(message, fallback_text="x")
    assert outcome.status == "failed"
    assert "HTTP 429" in (outcome.error or "")


def test_success_with_is_error_is_failed():
    message = SimpleNamespace(
        subtype="success",
        terminal_reason=None,
        result="API Error: 401 unauthorized",
        usage=None,
        errors=None,
        api_error_status=401,
        is_error=True,
    )
    outcome = outcome_from_result(message, fallback_text="x")
    assert outcome.status == "failed"
    assert "API Error" in (outcome.error or "")


def test_outcome_cancelled_and_failed():
    cancelled = SimpleNamespace(
        subtype="success",
        terminal_reason="aborted_streaming",
        result=None,
        usage=None,
        errors=None,
        api_error_status=None,
    )
    assert outcome_from_result(cancelled, fallback_text="x").status == "cancelled"
    failed = SimpleNamespace(
        subtype="error_during_execution",
        terminal_reason="api_error",
        result=None,
        usage=None,
        errors=["boom"],
        api_error_status=500,
    )
    outcome = outcome_from_result(failed, fallback_text="x")
    assert outcome.status == "failed"
    assert "boom" in (outcome.error or "")


def test_origin_kind_reads_dict_and_object():
    assert origin_kind(SimpleNamespace(origin={"kind": "task-notification"})) == (
        "task-notification"
    )
    assert origin_kind(SimpleNamespace(origin=SimpleNamespace(kind="human"))) == "human"
    assert origin_kind(SimpleNamespace(origin=None)) is None


def test_task_event_started_progress_updated_notification():
    started = task_event(
        SimpleNamespace(
            subtype="task_started",
            task_id="task-1",
            description="explore",
            tool_use_id="tool-1",
            task_type="agent",
            data={},
        )
    )
    assert started == {
        "type": "task_started",
        "task_id": "task-1",
        "tool_use_id": "tool-1",
        "description": "explore",
        "task_type": "agent",
    }

    progress = task_event(
        SimpleNamespace(
            subtype="task_progress",
            task_id="task-1",
            description="explore",
            tool_use_id="tool-1",
            last_tool_name="Read",
            usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 5},
            data={},
        )
    )
    assert progress["type"] == "task_progress"
    assert progress["last_tool_name"] == "Read"
    assert progress["usage"]["tool_uses"] == 1

    updated = task_event(
        SimpleNamespace(
            subtype="task_updated",
            task_id="task-1",
            patch={"status": "killed"},
            status=None,
            data={},
        )
    )
    assert updated == {"type": "task_updated", "task_id": "task-1", "status": "killed"}

    notification = task_event(
        SimpleNamespace(
            subtype="task_notification",
            task_id="task-1",
            tool_use_id="tool-1",
            status="completed",
            summary="done",
            usage=None,
            data={},
        )
    )
    assert notification["type"] == "task_notification"
    assert notification["status"] == "completed"
    assert notification["summary"] == "done"


def test_task_event_falls_back_to_data_and_ignores_others():
    message = SimpleNamespace(
        subtype="task_started",
        data={
            "task_id": "task-2",
            "description": "from-data",
            "tool_use_id": None,
            "task_type": None,
        },
    )
    event = task_event(message)
    assert event["task_id"] == "task-2"
    assert event["description"] == "from-data"
    assert task_event(SimpleNamespace(subtype="success", data={})) is None


def test_subagent_message_events_keep_text_and_tools_drop_thinking():
    message = AssistantMessage(
        [
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "s1", "name": "Read", "input": {"path": "a"}},
        ],
        parent_tool_use_id="parent-1",
    )
    assert subagent_message_events(message) == [
        {
            "type": "subagent_text",
            "parent_tool_use_id": "parent-1",
            "text": "working",
        },
        {
            "type": "subagent_tool_call",
            "parent_tool_use_id": "parent-1",
            "id": "s1",
            "name": "Read",
            "input": {"path": "a"},
        },
    ]


def test_subagent_message_without_parent_is_ignored():
    message = AssistantMessage([{"type": "text", "text": "top"}])
    assert subagent_message_events(message) == []
