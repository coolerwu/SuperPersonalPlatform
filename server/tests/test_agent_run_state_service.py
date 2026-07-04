from server.app.agent_run_state_service import AgentRunStateService


def test_run_state_service_writes_and_updates_run_artifact(tmp_path) -> None:
    service = AgentRunStateService(tmp_path)

    service.start_run(
        session_id="session-1",
        run_id="run-1",
        agent_id="assistant",
    )
    service.update_run(
        session_id="session-1",
        run_id="run-1",
        status="completed",
        final_response="answer",
        error={"kind": "failed", "message": "old"},
    )
    saved = service.update_run(
        session_id="session-1",
        run_id="run-1",
        error=None,
    )

    assert saved["schema_version"] == 1
    assert saved["id"] == "run-1"
    assert saved["session_id"] == "session-1"
    assert saved["agent_id"] == "assistant"
    assert saved["status"] == "completed"
    assert saved["final_response"] == "answer"
    assert saved["error"] is None
    assert (tmp_path / "agent_runs" / "session-1" / "run-1.json").exists()
