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
