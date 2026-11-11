from app.services.tool_artifacts import artifact_from_tool, collect_artifacts


def _workspace(tmp_path):
    workspace = tmp_path / "workspaces" / "default"
    workspace.mkdir(parents=True)
    return workspace


def test_write_tool_yields_a_file_artifact(tmp_path):
    workspace = _workspace(tmp_path)
    target = workspace / "report.md"
    target.write_text("hello")
    artifact = artifact_from_tool("Write", {"file_path": str(target)}, workspace)
    assert artifact == {
        "path": "report.md",
        "name": "report.md",
        "size": 5,
        "kind": "file",
    }


def test_relative_path_and_not_yet_written_file(tmp_path):
    workspace = _workspace(tmp_path)
    artifact = artifact_from_tool(
        "Edit", {"file_path": "./uploads/note.txt"}, workspace
    )
    assert artifact == {
        "path": "uploads/note.txt",
        "name": "note.txt",
        "size": None,
        "kind": "file",
    }


def test_screenshot_tool_uses_filename_and_marks_images(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "baidu.png").write_bytes(b"png")
    artifact = artifact_from_tool(
        "mcp__playwright__browser_take_screenshot",
        {"filename": "baidu.png"},
        workspace,
    )
    assert artifact == {
        "path": "baidu.png",
        "name": "baidu.png",
        "size": 3,
        "kind": "image",
    }


def test_ignores_other_tools_outside_paths_and_bad_input(tmp_path):
    workspace = _workspace(tmp_path)
    (tmp_path / "outside.png").write_bytes(b"png")
    assert artifact_from_tool("Bash", {"command": "touch x"}, workspace) is None
    assert (
        artifact_from_tool(
            "Write", {"file_path": str(tmp_path / "outside.png")}, workspace
        )
        is None
    )
    assert artifact_from_tool("Write", {"file_path": "../escape.txt"}, workspace) is None
    assert artifact_from_tool("Write", None, workspace) is None
    assert artifact_from_tool("Write", {"file_path": ""}, workspace) is None


def test_collect_dedupes_by_path_and_keeps_the_last(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "a.txt").write_text("x")
    blocks = [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "a.txt"}},
        {"type": "thinking", "thinking": "..."},
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": str(workspace / "a.txt")},
        },
        {"type": "text", "text": "done"},
    ]
    artifacts = collect_artifacts(blocks, workspace)
    assert [artifact["path"] for artifact in artifacts] == ["a.txt"]
    assert artifacts[0]["size"] == 1
