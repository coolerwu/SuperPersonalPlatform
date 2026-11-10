from app.services import sdk_bridge


def test_bridge_exposes_session_api():
    for name in ("list_sessions", "get_session_messages", "rename_session", "claude_client_class"):
        assert callable(getattr(sdk_bridge, name))


def test_bridge_imports_agent_sdk():
    assert sdk_bridge.sdk_version()
