import json
from datetime import datetime, timezone

import pytest

from server.app.context_knowledge_service import ContextKnowledgeError, ContextKnowledgeService
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


def test_search_context_returns_relevant_context_files(tmp_path) -> None:
    context_workspace = tmp_path / "context"
    files_dir = context_workspace / "knowledge" / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "wechat.md").write_text("微信 session 会保存在 workspace/sessions。", encoding="utf-8")
    (files_dir / "other.md").write_text("无关内容", encoding="utf-8")

    service = ContextKnowledgeService(context_workspace)
    hits = service.search("微信 session")

    assert hits
    assert hits[0].path == "/files/wechat.md"
    assert "workspace/sessions" in hits[0].snippet


def test_write_context_maps_tool_path_under_context_knowledge_files(tmp_path) -> None:
    service = ContextKnowledgeService(tmp_path / "context")

    result = service.write(
        type="knowledge",
        absolute_path="/files/wechat.md",
        content="微信绑定不同 Agent 时使用不同 session。",
        mode="create",
    )
    service.write(
        type="knowledge",
        absolute_path="/files/wechat.md",
        content="第二条记忆",
        mode="append",
    )

    path = tmp_path / "context" / "knowledge" / "files" / "wechat.md"
    assert result["path"] == "/files/wechat.md"
    assert path.read_text(encoding="utf-8") == "微信绑定不同 Agent 时使用不同 session。\n第二条记忆"


@pytest.mark.parametrize("absolute_path", ["/context/knowledge/files/a.md", "../a.md", "/files/../a.md"])
def test_write_context_rejects_paths_outside_tool_files_root(tmp_path, absolute_path) -> None:
    service = ContextKnowledgeService(tmp_path / "context")

    with pytest.raises(ContextKnowledgeError):
        service.write(type="knowledge", absolute_path=absolute_path, content="x", mode="append")


def test_platform_tool_runtime_builds_search_and_write_tools(tmp_path) -> None:
    tools = {
        tool.name: tool
        for tool in build_platform_tools(("search_context", "write_context"), context_workspace=tmp_path / "context")
    }

    write_result = tools["write_context"].invoke(
        {
            "type": "knowledge",
            "absolute_path": "/files/runtime.md",
            "content": "runtime 工具写入成功",
            "mode": "create",
        }
    )
    assert json.loads(write_result)["path"] == "/files/runtime.md"

    search_result = tools["search_context"].invoke({"query": "runtime", "top_k": 1})
    assert json.loads(search_result)["hits"][0]["path"] == "/files/runtime.md"


def test_search_context_returns_recent_webdav_documents_for_recent_notes(tmp_path) -> None:
    context_workspace = tmp_path / "context"
    webdav_cache = context_workspace / "webdav"
    (webdav_cache / "files" / "daily").mkdir(parents=True)
    (webdav_cache / "files" / "daily" / "today.md").write_text("# 今日笔记\n\n记录内容", encoding="utf-8")
    (webdav_cache / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "files": {
                    "/webdav/daily/today.md": {
                        "kind": "document",
                        "cache_path": "files/daily/today.md",
                        "modified": "2026-08-23T08:00:00+00:00",
                        "size": 24,
                        "permission_path": "/",
                        "protected": True,
                        "writable": False,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        """
auth:
  token: secret-token
nutstore:
  enabled: true
  base_url: https://dav.jianguoyun.com/dav/
  username: user@example.com
  password: secret
  root_path: /
context:
  webdav_sync:
    enabled: true
    root_path: /notebook
    interval_seconds: 600
  webdav_permissions:
    - path: /
      readable: true
      writable: false
      protected: true
""",
        encoding="utf-8",
    )
    tools = {
        tool.name: tool
        for tool in build_platform_tools(("search_context",), context_workspace=context_workspace)
    }

    result = json.loads(tools["search_context"].invoke({"query": "看看最近我的笔记", "top_k": 3}))

    assert result["recent_documents"][0]["path"] == "/webdav/daily/today.md"
    assert result["recent_documents"][0]["snippet"] == "# 今日笔记"


def test_write_context_description_keeps_memory_separate(tmp_path) -> None:
    tools = {
        tool.name: tool
        for tool in build_platform_tools(("write_context",), context_workspace=tmp_path / "context")
    }

    description = tools["write_context"].description

    assert "Do not use for personal memory" in description
    assert "write_file('/memories/...')" in description


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
    with pytest.raises(Exception):
        tools["schedule"].invoke({"action": "delete", "schedule_id": "foreign"})

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
