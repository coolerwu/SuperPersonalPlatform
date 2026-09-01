import json

from server.app.session_service import SessionService


def test_session_service_uses_active_binding_and_can_rotate_session(tmp_path) -> None:
    service = SessionService(tmp_path)
    first = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
        metadata={"to_user_id": "wxid_bot"},
    )
    again = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )

    assert again.session_id == first.session_id

    second = service.clear_active(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
        reason="清空上下文",
    )
    current = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )

    assert second.session_id != first.session_id
    assert current.session_id == second.session_id

    old_state = json.loads((tmp_path / "sessions" / first.session_id / "state.json").read_text(encoding="utf-8"))
    new_state = json.loads((tmp_path / "sessions" / second.session_id / "state.json").read_text(encoding="utf-8"))
    active = json.loads((tmp_path / "sessions" / "active.json").read_text(encoding="utf-8"))

    assert old_state["status"] == "archived"
    assert old_state["clear_reason"] == "清空上下文"
    assert new_state["status"] == "active"
    assert new_state["generation"] == 2
    assert active["bindings"][0]["session_id"] == second.session_id
    assert active["bindings"][0]["generation"] == 2


def test_session_service_adopts_legacy_deterministic_session_before_rotation(tmp_path) -> None:
    service = SessionService(tmp_path)
    legacy_id = service.build_session_id(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )
    legacy_dir = tmp_path / "sessions" / legacy_id
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "state.json").write_text(
        json.dumps(
            {
                "session_id": legacy_id,
                "status": "active",
                "message_count": 3,
                "run_count": 1,
                "created_at": "2026-08-01T00:00:00+00:00",
                "updated_at": "2026-08-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    current = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )
    rotated = service.clear_active(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )

    legacy_state = json.loads((legacy_dir / "state.json").read_text(encoding="utf-8"))

    assert current.session_id == legacy_id
    assert rotated.session_id != legacy_id
    assert legacy_state["status"] == "archived"
    assert legacy_state["message_count"] == 3


def test_session_service_lists_and_switches_only_matching_identity(tmp_path) -> None:
    service = SessionService(tmp_path)
    first = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )
    second = service.clear_active(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )
    other = service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_other",
        agent_id="assistant",
    )

    related = service.related_summaries_for_identity(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )
    related_ids = {item["session_id"] for item in related}

    assert first.session_id in related_ids
    assert second.session_id in related_ids
    assert other.session_id not in related_ids

    switched = service.switch_active(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
        selector=first.session_id,
    )
    current = service.active_summary(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_user",
        agent_id="assistant",
    )

    assert switched["session_id"] == first.session_id
    assert switched["active"] is True
    assert current["session_id"] == first.session_id

    try:
        service.switch_active(
            channel="wechat",
            channel_account_id="default",
            peer_type="private",
            peer_id="wxid_user",
            agent_id="assistant",
            selector=other.session_id,
        )
    except ValueError as exc:
        assert "session not found" in str(exc)
    else:
        raise AssertionError("expected identity boundary to reject unrelated session")
