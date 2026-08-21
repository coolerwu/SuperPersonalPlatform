import json

import pytest

from server.app.context_knowledge_service import ContextKnowledgeError, ContextKnowledgeService
from server.infrastructure.tool_runtime import build_platform_tools


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


def test_write_context_description_keeps_memory_separate(tmp_path) -> None:
    tools = {
        tool.name: tool
        for tool in build_platform_tools(("write_context",), context_workspace=tmp_path / "context")
    }

    description = tools["write_context"].description

    assert "Do not use for personal memory" in description
    assert "write_file('/memories/...')" in description
