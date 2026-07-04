import pytest

from server.app.chat_session_service import ChatSessionService
from server.domain.sessions import ChatMessageData
from server.domain.sessions import ChatSessionNotFoundError


def test_session_listing_is_filtered_by_agent(tmp_path) -> None:
    service = ChatSessionService(tmp_path)
    first = service.create_session("agent-a", "A")
    service.create_session("agent-b", "B")

    sessions = service.list_sessions("agent-a")

    assert [session.id for session in sessions] == [first.id]


def test_session_ownership_guard_rejects_other_agent(tmp_path) -> None:
    service = ChatSessionService(tmp_path)
    session = service.create_session("agent-a", "A")

    with pytest.raises(ChatSessionNotFoundError, match="does not belong"):
        service.get_session(session.id, "agent-b")

    assert service.get_session(session.id, "agent-a").id == session.id


def test_session_message_persists_run_id(tmp_path) -> None:
    service = ChatSessionService(tmp_path)
    session = service.create_session("agent-a", "A")

    service.append_message(
        session.id,
        ChatMessageData(role="assistant", content="answer", run_id="run-123"),
    )

    saved = service.get_session(session.id, "agent-a")
    assert saved.messages[-1].run_id == "run-123"
