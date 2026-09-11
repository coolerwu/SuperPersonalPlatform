import json


from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


def test_search_session_tool_scopes_to_current_session(tmp_path) -> None:
    from server.app.session_service import SessionService

    session_service = SessionService(tmp_path)
    current = session_service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_current",
        agent_id="assistant",
    )
    other = session_service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_other",
        agent_id="assistant",
    )
    session_service.append_message(
        current.session_id,
        role="user",
        content="刚才那张截图里提到 Lang Community 和财经新闻。",
        run_id="run_current",
    )
    session_service.append_message(
        other.session_id,
        role="user",
        content="Lang Community 这个词在别的会话里也出现过。",
        run_id="run_other",
    )
    tools = {
        tool.name: tool
        for tool in build_platform_tools(
            ("search_session",),
            context_workspace=tmp_path / "context",
            tool_context=PlatformToolContext(
                run_id="run_current",
                source="wechat",
                agent_id="assistant",
                session_id=current.session_id,
                metadata={},
            ),
        )
    }

    result = json.loads(tools["search_session"].invoke({"query": "Lang Community", "top_k": 5}))

    assert result["scope"] == "current"
    assert result["session_id"] == current.session_id
    assert [hit["run_id"] for hit in result["hits"]] == ["run_current"]
    assert "财经新闻" in result["hits"][0]["content"]


def test_search_session_tool_searches_related_sessions_with_jieba_terms(tmp_path) -> None:
    from server.app.session_service import SessionService

    session_service = SessionService(tmp_path)
    archived = session_service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_current",
        agent_id="assistant",
    )
    session_service.append_message(
        archived.session_id,
        role="user",
        content="每天早上九点从知识库里挑十篇随机文章发给我。",
        run_id="run_archived",
    )
    current = session_service.clear_active(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_current",
        agent_id="assistant",
    )
    session_service.append_message(
        current.session_id,
        role="user",
        content="当前会话只有普通闲聊。",
        run_id="run_current",
    )
    other = session_service.get_or_create(
        channel="wechat",
        channel_account_id="default",
        peer_type="private",
        peer_id="wxid_other",
        agent_id="assistant",
    )
    session_service.append_message(
        other.session_id,
        role="user",
        content="知识库文章这个词在其它微信联系人里出现过。",
        run_id="run_other",
    )
    tools = {
        tool.name: tool
        for tool in build_platform_tools(
            ("search_session",),
            context_workspace=tmp_path / "context",
            tool_context=PlatformToolContext(
                run_id="run_current",
                source="wechat",
                agent_id="assistant",
                session_id=current.session_id,
                metadata={},
            ),
        )
    }

    result = json.loads(
        tools["search_session"].invoke({"query": "知识库文章", "top_k": 5, "scope": "related"})
    )

    assert result["scope"] == "related"
    session_ids = [group["session"]["session_id"] for group in result["sessions"]]
    assert archived.session_id in session_ids
    assert other.session_id not in session_ids
    archived_group = next(group for group in result["sessions"] if group["session"]["session_id"] == archived.session_id)
    assert archived_group["session"]["active"] is False
    assert archived_group["hits"][0]["run_id"] == "run_archived"
    assert "知识库里挑十篇随机文章" in archived_group["hits"][0]["content"]


def test_lang_community_tools_are_registered_without_network_calls(tmp_path) -> None:
    tools = {
        tool.name: tool
        for tool in build_platform_tools(
            ("arxiv", "yahoo_finance_news"),
            context_workspace=tmp_path / "context",
        )
    }

    assert set(tools) == {"arxiv", "yahoo_finance_news"}
    assert "3 seconds" in tools["arxiv"].description
    assert "ticker" in tools["yahoo_finance_news"].description


def test_schedule_tool_manages_only_current_agent_session(tmp_path) -> None:
    service = FakeScheduleService()
    context = PlatformToolContext(
        run_id="run_current",
        source="wechat",
        agent_id="assistant",
        session_id="wechat_default_private_wxid",
        metadata={
            "account_id": "default",
            "from_user_id": "wxid",
            "peer_id": "wxid",
            "peer_type": "private",
            "context_token": "reply-token",
        },
    )
    tools = {
        tool.name: tool
        for tool in build_platform_tools(
            ("schedule",),
            context_workspace=tmp_path / "context",
            schedule_service=service,
            tool_context=context,
        )
    }

    created = json.loads(
        tools["schedule"].invoke(
            {
                "action": "create",
                "schedule_id": "morning_review",
                "name": "Morning Review",
                "prompt": "每天早上总结最近笔记",
                "trigger_kind": "interval",
                "interval_minutes": 60,
            }
        )
    )
    assert created["schedule_id"] == "morning_review"
    assert created["agent_id"] == "assistant"
    assert created["session_id"] == "wechat_default_private_wxid"
    assert created["delivery"]["channel"] == "wechat"
    assert created["delivery"]["to_user_id"] == "wxid"

    updated = json.loads(
        tools["schedule"].invoke(
            {
                "action": "update",
                "schedule_id": "morning_review",
                "prompt": "每天早上总结最近笔记和待办",
            }
        )
    )
    assert updated["prompt"] == "每天早上总结最近笔记和待办"
    assert updated["enabled"] is True

    listed = json.loads(tools["schedule"].invoke({"action": "list"}))
    assert [item["schedule_id"] for item in listed["schedules"]] == ["morning_review"]

    service.items["foreign"] = {
        "schema_version": 1,
        "id": "foreign",
        "type": "agent_run",
        "name": "Foreign",
        "enabled": True,
        "trigger": {"kind": "interval", "seconds": 3600},
        "agent_id": "assistant",
        "prompt": "别的会话创建的任务",
        "session_id": "other_session",
        "metadata": {
            "created_by": {
                "type": "agent_tool",
                "agent_id": "assistant",
                "session_id": "other_session",
            }
        },
    }
    denied = json.loads(tools["schedule"].invoke({"action": "delete", "schedule_id": "foreign"}))
    assert denied["ok"] is False
    assert denied["tool"] == "schedule"
    assert denied["error"]["message"] == "schedule is not owned by this agent/session"

    deleted = json.loads(tools["schedule"].invoke({"action": "delete", "schedule_id": "morning_review"}))
    assert deleted == {"schedule_id": "morning_review", "status": "deleted"}
    assert "morning_review" not in service.items


class FakeScheduleService:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def list_schedules(self):
        return [self._detail(item) for item in self.items.values()]

    def get_schedule(self, schedule_id: str):
        return self._detail(self.items[schedule_id])

    def create_schedule(self, payload: dict):
        self.items[payload["id"]] = {"type": "agent_run", **payload}
        return self.get_schedule(payload["id"])

    def update_schedule(self, schedule_id: str, payload: dict):
        self.items[schedule_id] = {"type": "agent_run", **payload, "id": schedule_id}
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: str) -> None:
        del self.items[schedule_id]

    def _detail(self, definition: dict):
        return {
            "definition": definition,
            "state": {
                "status": "idle",
                "next_run_at": "2026-08-25T00:00:00+00:00",
                "last_run_at": "",
                "last_run_id": "",
                "last_error": None,
            },
        }
