from types import SimpleNamespace

from app.services.sdk_events import StreamMapper, outcome_from_result


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
        {"type": "tool_call", "name": "Read", "input": {"file_path": "/tmp/x"}}
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
        {"type": "tool_call", "name": "Read", "input": {}}
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
        {"type": "tool_call", "name": "Read", "input": {}}
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
        {"type": "tool_call", "name": "First", "input": {"a": 1}}
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
        {"type": "tool_call", "name": "Second", "input": {"b": 2}}
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
        {"type": "tool_call", "name": "Read", "input": {}}
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
        {"type": "tool_call", "name": "", "input": {}}
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
