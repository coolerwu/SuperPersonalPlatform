# 聊天页展示 Agent 工作区图片与产物 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Agent 在回复里用相对路径引用的图片能直接显示，并在每条 assistant 回复下展示这一轮写入工作区的图片/文件（「产物」列表），实时与历史一致。

**Architecture:** 后端新增只读工作区文件下载路由（路径限定在 `workspaces/<agent_id>/`，可执行类型强制下载）；新增 `tool_artifacts` 服务，从 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 的 `file_path` 与截图类 MCP 工具的 `filename` 提取工作区内文件。历史消息在 `SessionCatalog.messages` 里附带 `artifacts`，实时流在 `ClaudeTurnRunner` 的 `tool_call` 事件上附带 `artifact`。前端 `Markdown` 把相对图片/文件链接改写到下载路由，`ChatPanel` 新增「产物」条；不引入 Claude Code hooks。

**Tech Stack:** Python 3.12 / FastAPI / pytest；React 18 / Vite / TypeScript。

**Spec：** 用户在 2026-11-11 的生产对话里发现 Playwright MCP 截图只显示 alt 文字「百度首页」；要求页面能看到 Agent 操作的图片与文件。落地时同步写入 `docs/intent.md` F2（Task 7）。

---

## 背景：为什么截图变成了 alt 文字

那条回复的内容是标准 Markdown：`![百度首页](baidu-mcp.png)`。截图文件由 MCP 保存在 Agent 工作区（SDK cwd 是 `workspaces/<agent_id>/`），但：

1. `apps/web/src/components/Markdown.tsx:56-58` 把 `src` 原样交给 `<img>`，浏览器按当前页面地址 `/chat/<uuid>` 解析成 `/baidu-mcp.png`；
2. 生产由 FastAPI 托管 `apps/web/dist`，`app/main.py:220-226` 对未命中的路径回退 `index.html`（HTML），`<img>` 加载失败，只剩 `alt` 文字；
3. 平台目前只有 `GET /api/agents/{agent_id}/uploads/{filename}` 一个文件路由（`apps/api/app/routers/agents.py:389`），且只服务 `uploads/` 目录，工作区其他文件（如 `baidu-mcp.png`）没有任何可访问 URL。

所以问题不是截图没生成，而是「工作区文件没有 URL」+「Markdown 相对路径没有被解析」。修复必须同时补这两块，否则换个文件名还是坏图。

**关于 hook：** 不需要 Claude Code 的 PostToolUse hook。平台 runner 已经拿到每个 `tool_call` 事件（`apps/api/app/services/sdk_events.py:60-69`），transcript 里也保留了 `tool_use` 块（`session_catalog._text_from_message`），hook 只会多一条需要平台读取的旁路。

**范围外：** MCP 工具直接返回图片内容（没有落盘）、Bash 重定向生成的文件、工作区之外的文件（如 `/tmp` 截图）都不在「产物」里展示；这些路径没有稳定 URL，硬做会引入文件搬运逻辑。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `apps/api/app/services/workspace_files.py`（新建） | 安全解析工作区相对路径；判定必须强制下载的媒体类型 |
| `apps/api/app/services/tool_artifacts.py`（新建） | 从工具名 + 输入提取工作区产物；历史与实时共用 |
| `apps/api/app/routers/agents.py`（修改） | 新增 `GET /api/agents/{agent_id}/files/{path}` |
| `apps/api/app/schemas.py`（修改） | `TranscriptMessageOut.artifacts` |
| `apps/api/app/services/session_catalog.py`（修改） | 历史 assistant 消息附带 `artifacts` |
| `apps/api/app/services/claude_runner.py`（修改） | `tool_call` 实时事件附带 `artifact` |
| `apps/web/src/workspaceUrl.ts`（新建） | 相对路径 → 下载路由 URL（纯函数） |
| `apps/web/src/components/Markdown.tsx`（修改） | `agentId` 属性；改写相对图片与文件链接 |
| `apps/web/src/components/ChatPanel.tsx`（修改） | 「产物」条；Markdown 传 `agentId` |
| `apps/web/src/App.tsx`、`apps/web/src/types.ts`（修改） | 实时产物状态与事件类型 |
| `apps/web/src/styles.css`（修改） | 「产物」条样式 |
| `docs/intent.md`、`CLAUDE.md`、`README.md`（修改） | 需求与文档同步 |
| `apps/api/app/tests/test_workspace_files.py`、`test_tool_artifacts.py`（新建），`test_session_catalog.py`、`test_claude_runner.py`（修改） | 后端测试 |

**约定：** 后端测试从仓库根目录运行 `.venv/bin/python -m pytest apps/api/app/tests -q`；每个任务结束提交一次；前端改完执行 `cd apps/web && npm run build` 并把 `dist` 一起提交。

---

### Task 1: 工作区文件解析与下载路由

**Files:**
- Create: `apps/api/app/services/workspace_files.py`
- Create: `apps/api/app/tests/test_workspace_files.py`
- Modify: `apps/api/app/routers/agents.py:389` 之后（上传路由下方）

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_workspace_files.py`：

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.workspace_files import media_type_for, resolve_workspace_file


def _workspace(tmp_path, agent="default"):
    workspace = tmp_path / "workspaces" / agent
    workspace.mkdir(parents=True)
    return workspace


def test_resolves_a_file_inside_the_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "baidu.png").write_bytes(b"png")
    target = resolve_workspace_file("default", "baidu.png", home=tmp_path)
    assert target == (workspace / "baidu.png").resolve()


def test_resolves_nested_paths(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "uploads").mkdir()
    (workspace / "uploads" / "note.txt").write_text("x")
    target = resolve_workspace_file("default", "uploads/note.txt", home=tmp_path)
    assert target == (workspace / "uploads" / "note.txt").resolve()


def test_rejects_traversal_symlinks_and_missing_files(tmp_path):
    workspace = _workspace(tmp_path)
    (tmp_path / "secret.txt").write_text("secret")
    assert resolve_workspace_file("default", "../secret.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", "/etc/passwd", home=tmp_path) is None
    assert resolve_workspace_file("missing", "x.txt", home=tmp_path) is None
    (workspace / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert resolve_workspace_file("default", "link.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", "nope.txt", home=tmp_path) is None
    assert resolve_workspace_file("default", ".", home=tmp_path) is None


def test_media_type_marks_scriptable_files_as_downloads():
    assert media_type_for(Path("a.png")) == ("image/png", False)
    assert media_type_for(Path("a.pdf")) == ("application/pdf", False)
    assert media_type_for(Path("a.html")) == ("application/octet-stream", True)
    assert media_type_for(Path("a.svg")) == ("application/octet-stream", True)
    assert media_type_for(Path("a.js")) == (
        "application/octet-stream",
        True,
    )


def test_workspace_file_is_served_inline(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nabc")
    response = client.get(f"/api/agents/{agent_id}/files/shot.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\nabc"


def test_workspace_file_route_forces_download_for_html(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "report.html").write_text("<script>alert(1)</script>")
    response = client.get(f"/api/agents/{agent_id}/files/report.html")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_workspace_file_route_rejects_traversal_and_unknown_agents(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    assert client.get(f"/api/agents/{agent_id}/files/missing.png").status_code == 404
    assert (
        client.get(f"/api/agents/{agent_id}/files/..%2F..%2Fdata%2Fsettings.json").status_code
        == 404
    )
    assert client.get("/api/agents/missing/files/a.png").status_code == 404


def test_workspace_file_route_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        response = unauthenticated.get("/api/agents/default/files/a.png")
        assert response.status_code == 401
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.workspace_files'`。

- [ ] **Step 3: 写解析模块**

创建 `apps/api/app/services/workspace_files.py`：

```python
"""Read-only access to files an Agent produced inside its workspace.

A relative image the Agent writes (``![图](chart.png)``) points at
``workspaces/<agent_id>/chart.png``, which the browser cannot fetch. The chat
resolves such paths to the download route; this module decides what may be
served and whether it must be forced into a download.

Scriptable media types are served as ``application/octet-stream`` with
``Content-Disposition: attachment``: a workspace HTML/SVG/JS file rendered
inline on the app origin could read the login cookie.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from pathlib import Path

from app.services.claude_env import workspace_for
from app.services.identifiers import valid_agent_id
from app.services.workspace import WorkspaceError, normalize_workspace_path

_UNSAFE_INLINE = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/javascript",
    "text/javascript",
    "application/xml",
    "text/xml",
}


def resolve_workspace_file(
    agent_id: str, relative_path: str, *, home: Path | None = None
) -> Path | None:
    """Resolve a workspace-relative file; ``None`` when it cannot be served."""
    if not valid_agent_id(agent_id):
        return None
    value = unicodedata.normalize("NFC", str(relative_path or "").strip())
    if value.startswith("./"):
        value = value[2:]
    try:
        normalize_workspace_path(value)
    except WorkspaceError:
        return None
    workspace = workspace_for(agent_id, home)
    if not workspace.is_dir():
        return None
    raw = workspace / value
    # Same rule as resolve_workspace_path: no symlink at the final component.
    if raw.is_symlink():
        return None
    candidate = raw.resolve(strict=False)
    if not candidate.is_relative_to(workspace.resolve()):
        return None
    if not candidate.is_file():
        return None
    return candidate


def media_type_for(path: Path) -> tuple[str, bool]:
    """``(media_type, must_download)`` for a workspace file."""
    guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if guessed in _UNSAFE_INLINE:
        return "application/octet-stream", True
    return guessed, False
```

- [ ] **Step 4: 运行解析模块测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -q -k "not route and not served and not download_for and not auth"`

Expected: 前 4 个测试 PASS。

- [ ] **Step 5: 加下载路由**

在 `apps/api/app/routers/agents.py` 顶部导入区加入（放在 `from app.services.settings_store import ...` 之后）：

```python
from urllib.parse import quote

from app.services.workspace_files import media_type_for, resolve_workspace_file
```

在 `get_upload` 函数（第 389-408 行）之后追加：

```python
@router.get("/{agent_id}/files/{path:path}")
async def get_workspace_file(
    agent_id: str, path: str, request: Request
) -> FileResponse:
    """Serve a file the Agent produced in its workspace, read-only.

    Only workspace-relative paths resolve; the agent itself writes with the
    SDK cwd set to this workspace, so markdown like ``![图](chart.png)`` maps
    here. Paused and archived Agents keep old files viewable.
    """
    snapshot = await _store(request).get()
    agent = _find_agent(snapshot.agents, agent_id, include_archived=True)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    target = resolve_workspace_file(agent_id, path)
    if target is None:
        raise HTTPException(status_code=404, detail="file not found")
    media_type, download = media_type_for(target)
    headers = {
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(target.name)}"
        )
    return FileResponse(target, media_type=media_type, headers=headers)
```

- [ ] **Step 6: 运行确认全部通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py apps/api/app/tests/test_attachments.py -q`

Expected: PASS。若 `mimetypes` 在 macOS 上把 `.js` 猜成 `text/javascript`，两值都在 `_UNSAFE_INLINE` 内，结果不变。

- [ ] **Step 7: 提交**

```bash
git add apps/api/app/services/workspace_files.py apps/api/app/tests/test_workspace_files.py apps/api/app/routers/agents.py
git commit -m "Serve Agent workspace files read-only"
```

---

### Task 2: 工具产物提取服务

**Files:**
- Create: `apps/api/app/services/tool_artifacts.py`
- Create: `apps/api/app/tests/test_tool_artifacts.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_tool_artifacts.py`：

```python
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
        artifact_from_tool("Write", {"file_path": str(tmp_path / "outside.png")}, workspace)
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_tool_artifacts.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tool_artifacts'`。

- [ ] **Step 3: 写实现**

创建 `apps/api/app/services/tool_artifacts.py`：

```python
"""Workspace files a turn produced, derived from tool inputs.

Only writes with an explicit path (``Write``/``Edit``/``MultiEdit``/
``NotebookEdit``) and screenshot/save-PDF MCP tools with a ``filename`` are
detected. Files a shell command or an MCP tool creates under another name are
invisible here; that is a documented limit, not a bug to paper over.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Iterable

from app.services.workspace import WorkspaceError, normalize_workspace_path

FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# SVG is a file, not a thumbnail: the download route refuses to render it
# inline, so an <img> would only ever be a broken image.
IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif", ".avif"}
)


def _candidate_path(name: str, tool_input: dict[str, Any]) -> str:
    if name in FILE_TOOLS:
        value = tool_input.get("file_path")
        return value if isinstance(value, str) else ""
    lowered = name.lower()
    if "screenshot" in lowered or "pdf_save" in lowered:
        value = tool_input.get("filename")
        if isinstance(value, str) and value.strip():
            return value
    return ""


def workspace_relative(raw: str, workspace: Path) -> str | None:
    """Normalize an absolute or relative tool path to a workspace-relative one."""
    value = unicodedata.normalize("NFC", str(raw or "").strip())
    if not value:
        return None
    if Path(value).is_absolute():
        try:
            value = Path(value).resolve(strict=False).relative_to(
                workspace.resolve()
            ).as_posix()
        except ValueError:
            return None
    elif value.startswith("./"):
        value = value[2:]
    try:
        return normalize_workspace_path(value)
    except WorkspaceError:
        return None


def artifact_from_tool(
    name: str, tool_input: Any, workspace: Path
) -> dict[str, Any] | None:
    if not isinstance(tool_input, dict):
        return None
    relative = workspace_relative(_candidate_path(name, tool_input), workspace)
    if relative is None:
        return None
    target = workspace.resolve() / relative
    return {
        "path": relative,
        "name": Path(relative).name,
        "size": target.stat().st_size if target.is_file() else None,
        "kind": (
            "image" if Path(relative).suffix.lower() in IMAGE_SUFFIXES else "file"
        ),
    }


def collect_artifacts(
    blocks: Iterable[Any], workspace: Path
) -> list[dict[str, Any]]:
    """Artifacts in first-seen order; a later write of the same path wins."""
    found: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        artifact = artifact_from_tool(
            str(block.get("name") or ""), block.get("input"), workspace
        )
        if artifact is not None:
            found[artifact["path"]] = artifact
    return list(found.values())
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_tool_artifacts.py -q`

Expected: 5 passed。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/tool_artifacts.py apps/api/app/tests/test_tool_artifacts.py
git commit -m "Derive workspace artifacts from tool inputs"
```

---

### Task 3: 历史消息附带产物

**Files:**
- Modify: `apps/api/app/schemas.py:267-272`
- Modify: `apps/api/app/services/session_catalog.py:93-143`
- Test: `apps/api/app/tests/test_session_catalog.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_session_catalog.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_messages_include_workspace_artifacts(tmp_path, monkeypatch):
    workspace = tmp_path / "workspaces" / "default"
    workspace.mkdir(parents=True)
    (workspace / "chart.png").write_bytes(b"png")
    path = transcript_path(SESSION_ID, "default", home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", "utf-8")
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: [
            SimpleNamespace(
                type="assistant",
                uuid="a1",
                parent_tool_use_id=None,
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "done"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Write",
                            "input": {"file_path": str(workspace / "chart.png")},
                        },
                    ],
                },
            )
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=tmp_path)
    rows = await catalog.messages(SESSION_ID, agent_id="default")
    assert rows[0]["artifacts"] == [
        {
            "path": "chart.png",
            "name": "chart.png",
            "size": 3,
            "kind": "image",
        }
    ]


@pytest.mark.asyncio
async def test_messages_without_agent_id_have_no_artifacts(monkeypatch):
    monkeypatch.setattr(
        sdk_bridge,
        "get_session_messages",
        lambda session_id: [
            SimpleNamespace(
                type="assistant",
                uuid="a1",
                parent_tool_use_id=None,
                message={
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Write",
                            "input": {"file_path": "/tmp/x.png"},
                        }
                    ],
                },
            )
        ],
    )
    catalog = session_catalog.SessionCatalog(FakeStore(), home=Path("/tmp"))
    rows = await catalog.messages("s1")
    assert rows[0]["artifacts"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_catalog.py -q -k artifacts`

Expected: FAIL — `KeyError: 'artifacts'`。

- [ ] **Step 3: 给 schema 加字段**

`apps/api/app/schemas.py` 第 267-272 行改为：

```python
class TranscriptMessageOut(BaseModel):
    id: str
    role: str
    content: str = ""
    blocks: list[dict] = []
    artifacts: list[dict] = []
```

- [ ] **Step 4: 在 catalog 里提取产物**

`apps/api/app/services/session_catalog.py` 顶部导入区加入：

```python
from app.services.tool_artifacts import collect_artifacts
```

在 `messages()` 里，`rows` 初始化之后、循环之前加：

```python
        workspace = (
            workspace_for(agent_id, self._home) if agent_id is not None else None
        )
```

用户消息行补一个空列表（第 109-116 行）：

```python
                rows.append(
                    {
                        "id": str(getattr(message, "uuid", "")),
                        "role": "user",
                        "content": content,
                        "blocks": [],
                        "artifacts": [],
                    }
                )
```

assistant 分支改为：

```python
            elif str(getattr(message, "type", "")) == "assistant":
                content, blocks = _text_from_message(
                    getattr(message, "message", {}) or {}
                )
                if not content.strip() and not blocks:
                    continue
                artifacts = (
                    collect_artifacts(blocks, workspace)
                    if workspace is not None
                    else []
                )
                # One turn can emit several assistant messages (thinking first,
                # then the answer, and one per tool round). The chat renders a
                # bubble per entry, so merge consecutive assistant messages
                # into the turn's single bubble.
                if rows and rows[-1]["role"] == "assistant":
                    previous = rows[-1]
                    merged = "\n\n".join(
                        part for part in (previous["content"], content) if part
                    )
                    previous["content"] = merged
                    previous["blocks"].extend(blocks)
                    previous["artifacts"] = _merge_artifacts(
                        previous["artifacts"], artifacts
                    )
                    continue
                rows.append(
                    {
                        "id": str(getattr(message, "uuid", "")),
                        "role": "assistant",
                        "content": content,
                        "blocks": blocks,
                        "artifacts": artifacts,
                    }
                )
```

在 `_session_title` 函数下方加辅助函数：

```python
def _merge_artifacts(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {item["path"]: item for item in existing}
    for item in incoming:
        merged[item["path"]] = item
    return list(merged.values())
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_session_catalog.py -q`

Expected: 全部 PASS（原有测试都不断言 `artifacts`，不受影响）。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/schemas.py apps/api/app/services/session_catalog.py apps/api/app/tests/test_session_catalog.py
git commit -m "Attach workspace artifacts to transcript history"
```

---

### Task 4: 实时流附带产物

**Files:**
- Modify: `apps/api/app/services/claude_runner.py:451`（mapper 初始化附近）、`:527-533`（事件循环）
- Test: `apps/api/app/tests/test_claude_runner.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_claude_runner.py` 末尾追加（`SimpleNamespace`、`FakeClient`、`ResultMessage`、`make_agent`、`make_turn`、`drain_until_end` 均已在该文件内）：

```python
@pytest.mark.asyncio
async def test_tool_call_events_carry_workspace_artifacts(tmp_path):
    await init_db()
    workspace = tmp_path / "workspaces" / "default"
    workspace.mkdir(parents=True)
    (workspace / "shot.png").write_bytes(b"png")
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "mcp__playwright__browser_take_screenshot",
                },
            }
        ),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"filename": "shot.png"}',
                },
            }
        ),
        SimpleNamespace(event={"type": "content_block_stop"}),
        ResultMessage(),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)
    tool_call = next(event for event in events if event["type"] == "tool_call")
    assert tool_call["name"] == "mcp__playwright__browser_take_screenshot"
    assert tool_call["artifact"] == {
        "path": "shot.png",
        "name": "shot.png",
        "size": 3,
        "kind": "image",
    }


@pytest.mark.asyncio
async def test_tool_call_events_without_files_have_no_artifact(tmp_path):
    await init_db()
    messages = [
        SimpleNamespace(
            event={
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Bash"},
            }
        ),
        SimpleNamespace(
            event={
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"command": "ls"}',
                },
            }
        ),
        SimpleNamespace(event={"type": "content_block_stop"}),
        ResultMessage(),
    ]
    runner = ClaudeTurnRunner(
        store=FakeStore(make_agent()),
        home=tmp_path,
        client_factory=lambda options: FakeClient(messages),
    )
    stream = TurnStream()
    queue = await stream.subscribe()
    await runner.run(make_turn(), stream)
    events = await drain_until_end(queue)
    tool_call = next(event for event in events if event["type"] == "tool_call")
    assert "artifact" not in tool_call
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py -q -k "carry_workspace_artifacts or have_no_artifact"`

Expected: 第一个 FAIL（`KeyError: 'artifact'`），第二个 PASS。

- [ ] **Step 3: 在 runner 里附加产物**

`apps/api/app/services/claude_runner.py` 顶部导入区（`from app.services.claude_env import ...` 之后）加入：

```python
from app.services.tool_artifacts import artifact_from_tool
```

`run()` 里 `mapper = StreamMapper()`（第 451 行）之后加：

```python
        workspace = workspace_for(turn.agent_id, self._home)
```

把第 527-533 行的流事件分发改为：

```python
                    event = getattr(message, "event", None)
                    if isinstance(event, dict):
                        for mapped in mapper.feed(event):
                            if mapped["type"] == "assistant_delta":
                                text_parts.append(mapped["delta"])
                            elif mapped["type"] == "tool_call":
                                artifact = artifact_from_tool(
                                    mapped["name"], mapped["input"], workspace
                                )
                                if artifact is not None:
                                    mapped["artifact"] = artifact
                            stream.publish(mapped)
                        continue
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_claude_runner.py apps/api/app/tests/test_sdk_events.py -q`

Expected: 全部 PASS。

- [ ] **Step 5: 后端全量测试**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add apps/api/app/services/claude_runner.py apps/api/app/tests/test_claude_runner.py
git commit -m "Attach workspace artifacts to live tool calls"
```

---

### Task 5: 前端解析相对图片与文件链接（修复截图）

**Files:**
- Create: `apps/web/src/workspaceUrl.ts`
- Modify: `apps/web/src/components/Markdown.tsx`
- Modify: `apps/web/src/components/ChatPanel.tsx`（6 处 `<Markdown` 调用传 `agentId`）

- [ ] **Step 1: 写 URL 辅助模块**

创建 `apps/web/src/workspaceUrl.ts`：

```ts
const EXTERNAL_URL = /^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|\/\/)/;

// Only links that name a file are treated as workspace files; a bare
// relative link such as "www.example.com" keeps its literal meaning.
const FILE_LINK =
  /\.(?:md|markdown|txt|pdf|csv|json|html?|xlsx?|docx?|pptx?|zip|png|jpe?g|gif|webp|bmp)$/i;

export function isRelativeWorkspaceUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("/api/")) {
    return false;
  }
  return !EXTERNAL_URL.test(trimmed);
}

/** `/api/agents/<id>/files/<workspace-relative path>` or null. */
export function workspaceFileUrl(agentId: string, raw: string): string | null {
  if (!agentId || !isRelativeWorkspaceUrl(raw)) return null;
  const relative = raw.trim().replace(/^\.\//, "").replace(/^\//, "");
  if (!relative) return null;
  const encoded = relative.split("/").map(encodeURIComponent).join("/");
  return `/api/agents/${encodeURIComponent(agentId)}/files/${encoded}`;
}

/** Workspace links only for names that look like files. */
export function workspaceLinkUrl(agentId: string, raw: string): string | null {
  if (!FILE_LINK.test(raw.trim())) return null;
  return workspaceFileUrl(agentId, raw);
}
```

- [ ] **Step 2: 让 Markdown 改写相对地址**

`apps/web/src/components/Markdown.tsx` 改为（完整文件）：

```tsx
import { Children, isValidElement, memo, ReactNode, useMemo, useState } from "react";
import ReactMarkdown, { Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

import { copyText } from "../clipboard";
import { workspaceFileUrl, workspaceLinkUrl } from "../workspaceUrl";

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const text = textOf(children);
  const first = Children.toArray(children)[0];
  const className = isValidElement<{ className?: string }>(first) ? first.props.className ?? "" : "";
  const language = /language-([\w-]+)/.exec(className)?.[1] ?? "";

  async function copy() {
    const copied = await copyText(text);
    setStatus(copied ? "copied" : "failed");
    window.setTimeout(() => setStatus("idle"), 1500);
  }

  return (
    <div className="code-block">
      <div className="code-block-head">
        <span>{language || "code"}</span>
        <button type="button" onClick={() => void copy()}>
          {status === "copied" ? "已复制" : status === "failed" ? "复制失败" : "复制"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function buildComponents(agentId?: string): Components {
  // Relative image paths are the Agent's workspace files. Without the
  // rewrite the browser asks the SPA route for them and gets index.html,
  // so the image silently degrades to its alt text.
  const imageSrc = (src?: string) =>
    agentId && src ? workspaceFileUrl(agentId, src) ?? src : src;
  const linkHref = (href?: string) =>
    agentId && href ? workspaceLinkUrl(agentId, href) ?? href : href;
  return {
    pre({ children }) {
      return <CodeBlock>{children}</CodeBlock>;
    },
    a({ href, children }) {
      return (
        <a href={linkHref(href)} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    img({ src, alt }) {
      return <img src={imageSrc(src)} alt={alt ?? ""} loading="lazy" />;
    },
  };
}

export const Markdown = memo(function Markdown({
  content,
  streaming = false,
  agentId,
}: {
  content: string;
  streaming?: boolean;
  /** Owner of workspace-relative image and file links. */
  agentId?: string;
}) {
  const components = useMemo(() => buildComponents(agentId), [agentId]);
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          // Only $$...$$ is math; single-dollar text such as "$100 and $120"
          // must stay plain text.
          [remarkMath, { singleDollarTextMath: false }],
          remarkBreaks,
        ]}
        rehypePlugins={
          streaming
            ? [[rehypeKatex, { throwOnError: false, strict: false }]]
            : [
                [rehypeKatex, { throwOnError: false, strict: false }],
                [rehypeHighlight, { detect: true, ignoreMissing: true }],
              ]
        }
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
```

- [ ] **Step 3: ChatPanel 的所有 Markdown 传入 agentId**

`apps/web/src/components/ChatPanel.tsx` 里 6 处 `<Markdown ...>` 全部加 `agentId`：

- 第 180 行：`<Markdown content={step.text} agentId={agentId} />`，并把 `AssistantProcess` 的 props 改为 `{ steps, agentId }: { steps: ProcessStep[]; agentId: string }`，调用处（第 675 行）改为 `<AssistantProcess steps={steps} agentId={props.agentId} />`；
- 第 247 行：`<Markdown content={reasoning} agentId={agentId} />`，`LiveProcess` props 增加 `agentId: string`，流式调用处（第 714 行附近）传 `agentId={props.agentId}`；
- 第 340 行：`{parsed.text && <Markdown content={parsed.text} agentId={agentId} />}`（`UserMessageRow` 已有 `agentId`）；
- 第 343 行：`<Markdown content={message.content} agentId={agentId} />`；
- 第 678 行：`{message.content && <Markdown content={message.content} agentId={props.agentId} />}`；
- 第 717 行：`<Markdown content={throttledStreamText} streaming agentId={props.agentId} />`。

- [ ] **Step 4: 构建确认类型通过**

Run: `cd apps/web && npm run build`

Expected: `tsc --noEmit` 与 `vite build` 均成功。

- [ ] **Step 5: 提交**

```bash
git add apps/web/src/workspaceUrl.ts apps/web/src/components/Markdown.tsx apps/web/src/components/ChatPanel.tsx
git commit -m "Resolve workspace-relative images and links in chat"
```

---

### Task 6: 页面「产物」列表

**Files:**
- Modify: `apps/web/src/types.ts:133-150`
- Modify: `apps/web/src/App.tsx:44-59`、`:601-645`、`:779-789`、`:1785-1830`
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/styles.css:3790-3860` 附近

- [ ] **Step 1: 类型与事件**

`apps/web/src/types.ts`：

```ts
export type MessageArtifact = {
  path: string;
  name: string;
  size: number | null;
  kind: "image" | "file";
};

export type TranscriptMessage = {
  id: string;
  role: string;
  content: string;
  blocks: Array<{ type: string; [key: string]: unknown }>;
  artifacts?: MessageArtifact[];
};
```

`TurnStreamEvent` 的 `tool_call` 分支改为：

```ts
  | {
      type: "tool_call";
      name: string;
      input: Record<string, unknown>;
      artifact?: MessageArtifact | null;
    }
```

（`MessageArtifact` 定义在 `TurnStreamEvent` 之前，保证可引用。）

- [ ] **Step 2: App 维护实时产物**

`apps/web/src/App.tsx`：

1. 导入类型：在 `import type { ... } from "./types";` 中加入 `MessageArtifact`。
2. `SessionLive` 增加字段并在 `EMPTY_LIVE` 初始化：

```ts
type SessionLive = {
  streamText: string;
  streamReasoning: string;
  activity: string[];
  artifacts: MessageArtifact[];
  notice: string;
  tracking: boolean;
};

const EMPTY_LIVE: SessionLive = {
  streamText: "",
  streamReasoning: "",
  activity: [],
  artifacts: [],
  notice: "",
  tracking: false,
};
```

3. 在 `EMPTY_LIVE` 之后加辅助函数：

```ts
function mergeArtifact(
  list: MessageArtifact[],
  artifact: MessageArtifact,
): MessageArtifact[] {
  const index = list.findIndex((item) => item.path === artifact.path);
  if (index < 0) return [...list, artifact];
  const next = list.slice();
  next[index] = artifact;
  return next;
}
```

4. `applyStreamEvent` 的 `tool_call` 分支：

```ts
      case "tool_call":
        updateLive(forSession, (current) => ({
          ...current,
          activity: [...current.activity, event.name],
          artifacts: event.artifact
            ? mergeArtifact(current.artifacts, event.artifact)
            : current.artifacts,
          notice: "",
        }));
        break;
```

5. `refreshTranscript` 的收尾 `updateLive` 增加一行（与 `activity` 同规则）：

```ts
        artifacts: streaming ? current.artifacts : [],
```

6. `<ChatPanel ...>` 传参加一行：`liveArtifacts={live.artifacts}`。

`Drawer` 仍接收 `activity` 字符串数组，本次不改。

- [ ] **Step 3: ChatPanel 渲染产物条**

`apps/web/src/components/ChatPanel.tsx`：

1. 类型导入改为 `import type { Attachment, MessageArtifact, TranscriptMessage } from "../types";`，并导入 `workspaceFileUrl`：`import { workspaceFileUrl } from "../workspaceUrl";`。
2. `ChatPanelProps` 增加 `liveArtifacts: MessageArtifact[];`。
3. 在 `FileIcon` 之后加组件：

```tsx
function ArtifactStrip({
  artifacts,
  agentId,
}: {
  artifacts: MessageArtifact[];
  agentId: string;
}) {
  if (artifacts.length === 0) return null;
  const images = artifacts.filter((artifact) => artifact.kind === "image");
  const files = artifacts.filter((artifact) => artifact.kind !== "image");
  return (
    <div className="message-artifacts">
      <div className="message-artifacts-label">产物</div>
      {images.length > 0 && (
        <div className="message-images">
          {images.map((artifact) => {
            const url = workspaceFileUrl(agentId, artifact.path) ?? "";
            return (
              <a
                className="message-image"
                key={artifact.path}
                href={url}
                target="_blank"
                rel="noreferrer"
                title={`打开 ${artifact.name}`}
              >
                <img src={url} alt={artifact.name} loading="lazy" />
              </a>
            );
          })}
        </div>
      )}
      {files.length > 0 && (
        <div className="message-files">
          {files.map((artifact) => (
            <a
              className="message-file"
              key={artifact.path}
              href={workspaceFileUrl(agentId, artifact.path) ?? ""}
              target="_blank"
              rel="noreferrer"
              title={artifact.path}
            >
              <FileIcon />
              <span className="message-file-name">{artifact.name}</span>
              {artifact.size !== null && (
                <span className="message-file-size">
                  {formatBytes(artifact.size)}
                </span>
              )}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
```

4. 历史 assistant 气泡里，在 `{message.content && <Markdown ... />}` 之后加：

```tsx
                <ArtifactStrip
                  artifacts={message.artifacts ?? []}
                  agentId={props.agentId}
                />
```

5. 流式气泡里，在 `props.streamText ? <Markdown .../> : <div className="muted">正在生成…</div>` 之后加：

```tsx
              <ArtifactStrip
                artifacts={props.liveArtifacts}
                agentId={props.agentId}
              />
```

- [ ] **Step 4: 样式**

在 `apps/web/src/styles.css` 的 `.message-file-size` 规则（约第 3851 行）之后追加：

```css
.message-artifacts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}

.message-artifacts-label {
  font-size: 12px;
  letter-spacing: 0.04em;
  color: var(--muted, #8a8fa3);
}

.message-artifacts .message-files,
.message-artifacts .message-images {
  margin: 0;
}

.message-artifacts .message-file {
  text-decoration: none;
}
```

- [ ] **Step 5: 构建与人工冒烟**

Run: `cd apps/web && npm run build`

Expected: 构建成功。

冒烟（开发模式）：

```bash
mkdir -p ~/.super-personal-platform/workspaces/default
printf 'workspace preview' > ~/.super-personal-platform/workspaces/default/note.txt
```

1. 启动后端 `cd apps/api && ../../.venv/bin/uvicorn app.main:app --reload --port 8000`，前端 `cd apps/web && npm run dev`。
2. 在聊天里让 Agent（或直接手写）回复 `![预览](note.txt)` 与 `[下载](note.txt)`：图片应请求 `/api/agents/<id>/files/note.txt` 并作为文本返回，链接点击可打开。
3. 让 Agent 执行 `Write` 写一个工作区文件：流式过程中气泡下方出现「产物」；刷新页面后历史气泡下方仍在。

Expected: 图片/文件请求 200；无 404；产物条可点击打开。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/types.ts apps/web/src/App.tsx apps/web/src/components/ChatPanel.tsx apps/web/src/styles.css apps/web/dist
git commit -m "Show Agent workspace artifacts under chat replies"
```

---

### Task 7: 文档同步与全量验证

**Files:**
- Modify: `docs/intent.md:74-97`（F2）
- Modify: `CLAUDE.md`（网页聊天条目）
- Modify: `README.md:63-79`（Chat 小节）

- [ ] **Step 1: 更新 `docs/intent.md` F2**

在「#### 功能点」第 13 条后追加：

```markdown
14. 工作区文件只读下载：`GET /api/agents/{agent_id}/files/{path}` 以工作区相对路径读取 `workspaces/<agent_id>/` 下的文件；拒绝绝对路径、`..`、符号链接逃逸、目录与不存在的文件。HTML、SVG、JS、XML 等可执行脚本的类型强制以 `application/octet-stream` + `Content-Disposition: attachment` 下载，避免在平台 origin 内联渲染。
15. assistant 回复里的相对图片/文件链接按当前 Agent 工作区解析（`![图](x.png)`、`[报告](report.md)`），图片直接显示、文件可点开；外部 URL（含协议、`//`、`/api/`）与锚点保持原样。每条 assistant 回复另附「产物」列表：从 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 的 `file_path` 与截图/存 PDF 类 MCP 工具的 `filename` 提取工作区内文件；历史来自 transcript 的 tool_use 块，实时来自带 `artifact` 字段的 `tool_call` 事件；同一路径去重、后写覆盖先写；工作区外的路径不展示。
```

在「#### 验收标准」末尾追加：

```markdown
- Agent 在工作区保存截图并在回复中用相对路径引用时，页面直接显示图片；点击可在新标签打开原文件。
- Agent 写入工作区文件后，回复下方出现「产物」列表（图片为缩略图，其他文件为文件卡片），流式过程中出现，刷新后仍在。
```

- [ ] **Step 2: 更新 `CLAUDE.md` 网页聊天条目**

在「网页聊天」条目末尾追加一句：

```markdown
Agent 回复里的相对图片/文件链接按当前 Agent 工作区解析，经 `GET /api/agents/{agent_id}/files/{path}` 读取（可执行类型强制下载）；assistant 消息附带「产物」列表，来自 tool_use 的 `file_path` 与截图类工具 `filename`，实时事件 `tool_call.artifact`，工作区外路径不展示。
```

- [ ] **Step 3: 更新 `README.md` Chat 小节**

在「附件」条目后追加：

```markdown
- Agent 产物：回复里的相对图片/文件链接按当前 Agent 工作区解析，图片直接显示、文件可点开；文件通过 `GET /api/agents/{agent_id}/files/{path}` 只读读取，HTML/SVG/JS/XML 等类型强制下载。回复下方另列「产物」（来自 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 与截图/存 PDF 类 MCP 工具），同一路径去重；工作区外文件与 MCP 直接返回的图片不在列表中。
```

- [ ] **Step 4: 全量测试与构建**

Run: `.venv/bin/python -m pytest apps/api/app/tests -q`

Expected: 全部 PASS。

Run: `cd apps/web && npm run build`

Expected: 构建成功，`dist` 更新。

- [ ] **Step 5: 提交**

```bash
git add docs/intent.md CLAUDE.md README.md apps/web/dist
git commit -m "Document workspace file preview and artifacts"
```

---

## 自审清单

- **需求覆盖：** 截图不显示 → Task 1（路由）+ Task 5（Markdown 改写）；页面展示 Agent 操作的图片/文件 → Task 2/3/4（数据）+ Task 6（UI）。hook 问题在背景中说明不需要。
- **无占位符：** 每个代码步骤给出完整实现与测试；命令与预期输出齐全。
- **类型/命名一致性：** 后端产物字段统一为 `{path, name, size, kind}`；`resolve_workspace_file(agent_id, relative_path, home=...)`、`media_type_for(path) -> (media_type, download)`、`artifact_from_tool(name, tool_input, workspace)`、`collect_artifacts(blocks, workspace)` 在路由、catalog、runner、测试中用法一致；前端 `MessageArtifact` 在 `types.ts`、SSE 事件、App 与 ChatPanel 中同名同形。
- **已知风险：**
  1. 工作区 HTML/SVG 不能内联预览（安全权衡），在 intent 与 README 中写明。
  2. Playwright MCP 未传 `filename` 时截图落在 MCP 自己的输出目录，不会出现在「产物」；回复里手写的绝对 `/tmp` 路径也不会被改写（浏览器端只改写相对路径）。这是有意为之。
  3. `Markdown` 只改写「像文件」的相对链接，避免把普通相对网址误当工作区文件。
  4. 产物在 `tool_call` 到达时立即展示，若该次写入随后失败，条目仍会短暂出现；历史刷新后以文件是否存在为准（`size` 为 `null`）。
