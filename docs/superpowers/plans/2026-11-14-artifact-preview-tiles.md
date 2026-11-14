# 聊天产物方块预览实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把回复末尾「产物」从行内小链接改成方块网格：文本展示内容并可滚动，Markdown 渲染，图片缩略图，其余文件显示类型图标。

**Architecture:** 后端新增只读文本预览接口（扩展名白名单 + 512 KB 截断 + UTF-8 解码），前端新增 `ArtifactGrid` 组件，按文件类型渲染三种方块，预览结果懒加载并按 `agentId + path + size` 缓存。旧的文件下载接口保持不变。

**Tech Stack:** FastAPI（Python 3.12）、React 18 + TypeScript + Vite、pytest。

**依据设计：** `docs/superpowers/specs/2026-11-14-artifact-preview-tiles-design.md`

---

## 文件结构

### 后端

- `apps/api/app/services/workspace_files.py`：文本扩展名白名单、`preview_format()`、`read_text_preview()`。
- `apps/api/app/routers/agents.py`：`GET /api/agents/{id}/file-preview/{path:path}`。
- `apps/api/app/tests/test_workspace_files.py`：helper 与路由测试。

### 前端

- `apps/web/src/api.ts`：`previewWorkspaceFile()`。
- `apps/web/src/types.ts`：`ArtifactPreview`。
- `apps/web/src/workspaceUrl.ts`：把文件内相对链接按文件目录解析。
- `apps/web/src/components/Markdown.tsx`：接受 `basePath` 并用于相对图片/链接。
- `apps/web/src/artifactTypes.ts`：文本/Markdown 扩展名判断、类型徽标。
- `apps/web/src/components/ArtifactGrid.tsx`：网格、三种方块、预览缓存。
- `apps/web/src/components/MessageList.tsx`：用 `ArtifactGrid` 替换 `ArtifactStrip`。
- `apps/web/src/styles.css`：网格、方块、Markdown 紧凑样式、移动端。

---

## Task 1: F23 需求记录

**Files:**
- Modify: `docs/intent.md`（追加到 F22 之后）

- [ ] **Step 1: 在 `docs/intent.md` 末尾追加 F23**

```markdown
### F23 聊天产物方块预览

- 状态：现行
- 日期：2026-11-14
- 来源：2026-11-14 产物方块预览设计

#### 功能点

1. 回复末尾的「产物」使用响应式方块网格展示；每个方块显示类型徽标（如 MD/PY/PDF）、文件名和大小，并提供打开链接。
2. 文本文件在方块内直接展示内容并可在方块内滚动；超过 512 KB 时只展示前 512 KB（见第 4 条）。`.md` 按 Markdown 渲染，其他文本按原样等宽显示；`.md` 内的相对图片和链接按该 Markdown 文件所在目录解析。
3. 图片使用缩略图（不裁切）；PDF、Office、压缩包等其余文件显示类型图标。
4. 文本预览由服务端按扩展名白名单提供（Markdown、纯文本、常见代码/配置/日志/CSV/HTML 等文本类），最多 512 KB；超过上限时方块标注已截断。
5. 预览在方块进入可视区后才加载，并在当前页面内缓存（滚出再滚回不重复请求）；流式写入中的文件在写入完成后刷新预览；加载失败或文件已删除时回落到类型图标，不中断聊天。

#### 验收标准

- 一条回复同时产出 `.md`、`.py`、`.json`、`.png`、`.pdf` 时，分别显示 Markdown 渲染、等宽文本、等宽文本、图片缩略图、PDF 图标，且每个方块可见类型徽标、文件名、大小与打开链接。
- 文本方块内可滚动查看已加载的内容；超过 512 KB 显示截断提示。
- 子目录中的 `.md` 以相对路径引用同目录图片时，预览内图片正常显示。
- 图片不裁切，PDF 等文件不发起预览请求。
- 预览请求只在方块进入可视区后发起；同一页面内滚出再滚回不重复请求，刷新后重新加载。
- 删除产物文件或预览失败后，方块回落为类型图标并保留「打开」链接，聊天不报错、不出现错误弹层。
- 空文件显示「空文件」。
- ≤860px 宽度下为两列（更宽时列数自动增加），可滚动、可打开；桌面悬停方块有可见反馈；「打开」链接可 Tab 聚焦并有可见焦点框；文本方块主体可键盘聚焦并滚动。
- 后端测试与前端构建通过。
```

- [ ] **Step 2: 提交**

```bash
git add docs/intent.md
git commit -m "Record artifact preview tile requirements

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: 文本预览 helper

**Files:**
- Modify: `apps/api/app/services/workspace_files.py`
- Test: `apps/api/app/tests/test_workspace_files.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_workspace_files.py` 末尾追加：

```python
def test_preview_format_recognizes_markdown_and_text():
    assert preview_format(Path("a.md")) == "markdown"
    assert preview_format(Path("a.MARKDOWN")) == "markdown"
    assert preview_format(Path("a.py")) == "text"
    assert preview_format(Path("a.json")) == "text"
    assert preview_format(Path("a.svg")) == "text"
    assert preview_format(Path("a.pdf")) is None
    assert preview_format(Path("a.zip")) is None


def test_read_text_preview_returns_content_and_size(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("# 标题\n第二行", "utf-8")
    content, truncated, size = read_text_preview(target)
    assert content == "# 标题\n第二行"
    assert truncated is False
    assert size == target.stat().st_size


def test_read_text_preview_truncates_at_512_kb(tmp_path):
    target = tmp_path / "big.log"
    target.write_bytes(b"x" * (512 * 1024 + 10))
    content, truncated, size = read_text_preview(target)
    assert truncated is True
    assert len(content) == 512 * 1024
    assert size == 512 * 1024 + 10


def test_read_text_preview_replaces_invalid_utf8_and_strips_bom(tmp_path):
    target = tmp_path / "bad.txt"
    target.write_bytes(b"\xef\xbb\xbfok\xffbad")
    content, truncated, size = read_text_preview(target)
    assert content == "ok�bad"
    assert truncated is False
    assert size == 9
```

把首行 import 改为：

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services.workspace_files import (
    media_type_for,
    preview_format,
    read_text_preview,
    resolve_workspace_file,
)
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -q
```

Expected: FAIL，`ImportError: cannot import name 'preview_format'`。

- [ ] **Step 3: 实现 helper**

在 `apps/api/app/services/workspace_files.py` 的 `_UNSAFE_INLINE` 之后加入：

```python
_PREVIEW_LIMIT = 512 * 1024
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".svg", ".html", ".htm", ".css", ".scss",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".zsh",
    ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
    ".rb", ".php", ".lua", ".swift", ".kt", ".r", ".m", ".tex",
}


def preview_format(path: Path) -> str | None:
    """``"markdown"``/``"text"`` for previewable files, else ``None``."""
    suffix = path.suffix.lower()
    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return None


def read_text_preview(path: Path) -> tuple[str, bool, int]:
    """Return ``(content, truncated, size)``; never raises on bad UTF-8."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(_PREVIEW_LIMIT + 1)
    truncated = len(raw) > _PREVIEW_LIMIT
    if truncated:
        raw = raw[:_PREVIEW_LIMIT]
    content = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    return content, truncated, size
```

- [ ] **Step 4: 运行确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -q
```

Expected: PASS（新增 4 个用例）。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/workspace_files.py apps/api/app/tests/test_workspace_files.py
git commit -m "Add text preview helpers for workspace files

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: 预览路由

**Files:**
- Modify: `apps/api/app/routers/agents.py`
- Test: `apps/api/app/tests/test_workspace_files.py`

- [ ] **Step 1: 写失败测试**

在 `apps/api/app/tests/test_workspace_files.py` 末尾追加：

```python
def test_file_preview_returns_markdown_content(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "report.md").write_text("# 标题\n正文", "utf-8")
    response = client.get(f"/api/agents/{agent_id}/file-preview/report.md")
    assert response.status_code == 200
    assert response.json() == {
        "content": "# 标题\n正文",
        "format": "markdown",
        "truncated": False,
        "size": len("# 标题\n正文".encode()),
    }
    assert response.headers["cache-control"] == "private, no-cache"


def test_file_preview_returns_plain_text_for_code(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "run.py").write_text("print('hi')\n", "utf-8")
    response = client.get(f"/api/agents/{agent_id}/file-preview/run.py")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "text"
    assert body["content"] == "print('hi')\n"


def test_file_preview_truncates_large_text(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "big.log").write_bytes(b"x" * (512 * 1024 + 1))
    response = client.get(f"/api/agents/{agent_id}/file-preview/big.log")
    body = response.json()
    assert response.status_code == 200
    assert body["truncated"] is True
    assert len(body["content"]) == 512 * 1024
    assert body["size"] == 512 * 1024 + 1


def test_file_preview_rejects_binary_types(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    workspace = settings.workspaces_dir / agent_id
    (workspace / "doc.pdf").write_bytes(b"%PDF-1.4")
    (workspace / "bundle.zip").write_bytes(b"PK\x03\x04")
    for name in ("doc.pdf", "bundle.zip"):
        response = client.get(f"/api/agents/{agent_id}/file-preview/{name}")
        assert response.status_code == 415


def test_file_preview_rejects_missing_and_traversal(client):
    agent_id = client.get("/api/agents").json()[0]["id"]
    assert (
        client.get(f"/api/agents/{agent_id}/file-preview/missing.md").status_code
        == 404
    )
    assert (
        client.get(
            f"/api/agents/{agent_id}/file-preview/..%2F..%2Fdata%2Fsettings.json"
        ).status_code
        == 404
    )
    assert (
        client.get("/api/agents/missing/file-preview/a.md").status_code == 404
    )


def test_file_preview_allows_paused_agent(client):
    agent = client.post("/api/agents", json={"name": "PausedPreview"}).json()
    assert (
        client.put(
            f"/api/agents/{agent['id']}", json={"status": "paused"}
        ).status_code
        == 200
    )
    workspace = settings.workspaces_dir / agent["id"]
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "note.txt").write_text("still readable", "utf-8")
    response = client.get(f"/api/agents/{agent['id']}/file-preview/note.txt")
    assert response.status_code == 200
    assert response.json()["content"] == "still readable"


def test_file_preview_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        response = unauthenticated.get(
            "/api/agents/default/file-preview/a.md"
        )
        assert response.status_code == 401
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -k file_preview -q
```

Expected: FAIL，404（路由不存在）。

- [ ] **Step 3: 增加路由**

在 `apps/api/app/routers/agents.py`：

1. 把 `import mimetypes` 之前补上 `import asyncio`（保持字母序）。
2. 把 `from fastapi import ...` 改为包含 `Response`：

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
```

3. 把 `from app.services.workspace_files import media_type_for, resolve_workspace_file` 改为：

```python
from app.services.workspace_files import (
    media_type_for,
    preview_format,
    read_text_preview,
    resolve_workspace_file,
)
```

4. 在 `get_workspace_file` 之后加入：

```python
@router.get("/{agent_id}/file-preview/{path:path}")
async def preview_workspace_file(
    agent_id: str,
    path: str,
    request: Request,
    response: Response,
) -> dict[str, object]:
    """Return capped plain text for a workspace file the chat renders in a tile."""
    snapshot = await _store(request).get()
    agent = _find_agent(snapshot.agents, agent_id, include_archived=True)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    target = resolve_workspace_file(agent_id, path)
    if target is None:
        raise HTTPException(status_code=404, detail="file not found")
    fmt = preview_format(target)
    if fmt is None:
        raise HTTPException(status_code=415, detail="file type cannot be previewed")
    try:
        content, truncated, size = await asyncio.to_thread(
            read_text_preview, target
        )
    except OSError as exc:
        raise HTTPException(status_code=404, detail="file not found") from exc
    response.headers["Cache-Control"] = "private, no-cache"
    return {
        "content": content,
        "format": fmt,
        "truncated": truncated,
        "size": size,
    }
```

- [ ] **Step 4: 运行确认通过**

```bash
.venv/bin/python -m pytest apps/api/app/tests/test_workspace_files.py -q
.venv/bin/python -m pytest apps/api/app/tests -q
```

Expected: 文件测试全过；全量测试通过（此前 820 passed, 3 skipped，新增 4 + 7 个用例后约 831 passed）。

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/routers/agents.py apps/api/app/tests/test_workspace_files.py
git commit -m "Add workspace file text preview endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: 前端 API、类型、文件判断与 Markdown 基础路径

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/workspaceUrl.ts`
- Modify: `apps/web/src/components/Markdown.tsx`
- Create: `apps/web/src/artifactTypes.ts`

- [ ] **Step 1: 增加 `ArtifactPreview`**

在 `types.ts` 的 `MessageArtifact` 之后加入：

```ts
export type ArtifactPreview = {
  content: string;
  format: "markdown" | "text";
  truncated: boolean;
  size: number;
};
```

- [ ] **Step 2: 增加 API 方法**

在 `api.ts` 的类型 import 块加入 `ArtifactPreview`（按字母序放在 `AppSettings`/`Attachment` 附近），并在 `listAgents` 附近加入：

```ts
  previewWorkspaceFile: (agentId: string, path: string, signal?: AbortSignal) =>
    request<ArtifactPreview>(
      `/api/agents/${encodeURIComponent(agentId)}/file-preview/${path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
      signal ? { signal } : {},
    ),
```

- [ ] **Step 3: 新建 `artifactTypes.ts`**

```ts
const MARKDOWN_SUFFIXES = new Set([".md", ".markdown"]);

// Keep in sync with _TEXT_EXTENSIONS in apps/api/app/services/workspace_files.py.
const TEXT_SUFFIXES = new Set([
  ".md", ".markdown", ".txt", ".log", ".csv", ".tsv",
  ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
  ".xml", ".svg", ".html", ".htm", ".css", ".scss",
  ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".zsh",
  ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
  ".rb", ".php", ".lua", ".swift", ".kt", ".r", ".m", ".tex",
]);

export function artifactSuffix(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

export function isMarkdownArtifact(name: string): boolean {
  return MARKDOWN_SUFFIXES.has(artifactSuffix(name));
}

export function isTextArtifact(name: string): boolean {
  return TEXT_SUFFIXES.has(artifactSuffix(name));
}

export function artifactBadge(name: string): string {
  const suffix = artifactSuffix(name);
  if (suffix === ".md" || suffix === ".markdown") return "MD";
  if (!suffix) return "FILE";
  return suffix.slice(1).toUpperCase().slice(0, 4);
}
```

- [ ] **Step 4: Markdown 按文件目录解析相对链接**

在 `apps/web/src/workspaceUrl.ts` 末尾加入：

```ts
/** Resolve a relative link inside a workspace file against that file's directory. */
export function resolveRelativeToFile(
  basePath: string | undefined,
  raw: string,
): string {
  const value = (raw ?? "").trim();
  if (!basePath || !value || value.startsWith("#") || value.startsWith("/")) {
    return raw;
  }
  if (EXTERNAL_URL.test(value)) return raw;
  const dir = basePath.includes("/")
    ? basePath.slice(0, basePath.lastIndexOf("/") + 1)
    : "";
  if (!dir) return raw;
  try {
    const resolved = new URL(value, `http://workspace/${dir}`);
    return (
      decodeURIComponent(resolved.pathname.replace(/^\//, "")) +
      resolved.search +
      resolved.hash
    );
  } catch {
    return raw;
  }
}
```

`apps/web/src/components/Markdown.tsx` 只需三处小改，保留文件顶部的其他 import 与 `textOf`/`CodeBlock`：

1. 把 `import { workspaceFileUrl, workspaceLinkUrl } from "../workspaceUrl";` 改为：

```ts
import {
  resolveRelativeToFile,
  workspaceFileUrl,
  workspaceLinkUrl,
} from "../workspaceUrl";
```

2. 把 `buildComponents` 的函数头与两个 URL 辅助函数改为：

```tsx
function buildComponents(agentId?: string, basePath?: string): Components {
  const imageSrc = (src?: string) =>
    agentId && src
      ? workspaceFileUrl(agentId, resolveRelativeToFile(basePath, src)) ?? src
      : src;
  const linkHref = (href?: string) =>
    agentId && href
      ? workspaceLinkUrl(agentId, resolveRelativeToFile(basePath, href)) ?? href
      : href;
```

3. 把 `Markdown` 组件的签名与 `useMemo` 改为：

```tsx
export const Markdown = memo(function Markdown({
  content,
  streaming = false,
  agentId,
  basePath,
}: {
  content: string;
  streaming?: boolean;
  /** Owner of workspace-relative image and file links. */
  agentId?: string;
  /** Workspace path of the file this Markdown came from, for relative links. */
  basePath?: string;
}) {
  const components = useMemo(
    () => buildComponents(agentId, basePath),
    [agentId, basePath],
  );
```

其余 JSX 保持不变。

- [ ] **Step 5: 构建确认类型通过**

```bash
cd apps/web && npm run build
```

Expected: 构建成功；`artifactTypes` 还未被使用，允许存在。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/api.ts apps/web/src/types.ts apps/web/src/workspaceUrl.ts apps/web/src/components/Markdown.tsx apps/web/src/artifactTypes.ts apps/web/dist
git commit -m "Add artifact preview client and relative Markdown links

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: ArtifactGrid 组件与样式

**Files:**
- Create: `apps/web/src/components/ArtifactGrid.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 新建组件**

创建 `apps/web/src/components/ArtifactGrid.tsx`：

```tsx
import { useEffect, useState } from "react";

import { api } from "../api";
import { artifactBadge, isMarkdownArtifact, isTextArtifact } from "../artifactTypes";
import { formatBytes } from "../messageContent";
import type { ArtifactPreview, MessageArtifact } from "../types";
import { workspaceFileUrl } from "../workspaceUrl";
import { Markdown } from "./Markdown";

const PREVIEW_CACHE_LIMIT = 40;
const previewCache = new Map<string, ArtifactPreview>();

function previewKey(agentId: string, artifact: MessageArtifact): string {
  return `${agentId}:${artifact.path}:${artifact.size ?? 0}`;
}

function cachePreview(key: string, preview: ArtifactPreview) {
  if (previewCache.size >= PREVIEW_CACHE_LIMIT) {
    const oldest = previewCache.keys().next().value;
    if (oldest) previewCache.delete(oldest);
  }
  previewCache.set(key, preview);
}

type PreviewState =
  | { status: "loading" }
  | { status: "ready"; preview: ArtifactPreview }
  | { status: "error" };

function useArtifactPreview(
  agentId: string,
  artifact: MessageArtifact,
  enabled: boolean,
): PreviewState {
  const key = previewKey(agentId, artifact);
  const [state, setState] = useState<PreviewState>(() => {
    const cached = previewCache.get(key);
    return cached ? { status: "ready", preview: cached } : { status: "loading" };
  });

  useEffect(() => {
    if (!enabled) return;
    const cached = previewCache.get(key);
    if (cached) {
      setState({ status: "ready", preview: cached });
      return;
    }
    let cancelled = false;
    let retry: number | undefined;
    const controller = new AbortController();
    setState({ status: "loading" });

    const load = async (attempt: number) => {
      try {
        const preview = await api.previewWorkspaceFile(
          agentId,
          artifact.path,
          controller.signal,
        );
        if (cancelled) return;
        cachePreview(key, preview);
        setState({ status: "ready", preview });
      } catch {
        if (cancelled) return;
        if (attempt === 0) {
          retry = window.setTimeout(() => void load(1), 1000);
          return;
        }
        setState({ status: "error" });
      }
    };

    void load(0);
    return () => {
      cancelled = true;
      controller.abort();
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [agentId, artifact.path, enabled, key]);

  return state;
}

function FileGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="26"
      height="26"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function TileHead({ artifact }: { artifact: MessageArtifact }) {
  return (
    <div className="artifact-tile-head">
      <span className="artifact-badge">{artifactBadge(artifact.name)}</span>
      <span className="artifact-name" title={artifact.path}>
        {artifact.name}
      </span>
    </div>
  );
}

function TileFoot({
  artifact,
  href,
  note,
}: {
  artifact: MessageArtifact;
  href: string;
  note?: string;
}) {
  return (
    <div className="artifact-tile-foot">
      <span className="artifact-size">{formatBytes(artifact.size)}</span>
      {note && <span className="artifact-note">{note}</span>}
      <a
        className="artifact-open"
        href={href}
        target="_blank"
        rel="noreferrer"
        aria-label={`打开 ${artifact.name}`}
      >
        打开
      </a>
    </div>
  );
}

function TextArtifactTile({
  artifact,
  agentId,
  href,
}: {
  artifact: MessageArtifact;
  agentId: string;
  href: string;
}) {
  const markdown = isMarkdownArtifact(artifact.name);
  const preview = useArtifactPreview(agentId, artifact, true);

  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <div
        className="artifact-tile-body"
        tabIndex={0}
        aria-label={`${artifact.name} 内容预览`}
      >
        {preview.status === "loading" && (
          <div className="artifact-skeleton" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
        )}
        {preview.status === "error" && (
          <div className="artifact-empty">无法预览</div>
        )}
        {preview.status === "ready" &&
          (preview.preview.content ? (
            markdown ? (
              <div className="artifact-tile-markdown">
                <Markdown
                  content={preview.preview.content}
                  agentId={agentId}
                  basePath={artifact.path}
                />
              </div>
            ) : (
              <pre className="artifact-tile-text">
                {preview.preview.content}
              </pre>
            )
          ) : (
            <div className="artifact-empty">空文件</div>
          ))}
      </div>
      <TileFoot
        artifact={artifact}
        href={href}
        note={
          preview.status === "ready" && preview.preview.truncated
            ? "已截断前 512 KB"
            : undefined
        }
      />
    </div>
  );
}

function ImageArtifactTile({
  artifact,
  href,
}: {
  artifact: MessageArtifact;
  href: string;
}) {
  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <a
        className="artifact-tile-image"
        href={href}
        target="_blank"
        rel="noreferrer"
        title={`打开 ${artifact.name}`}
      >
        <img src={href} alt={artifact.name} loading="lazy" />
      </a>
      <TileFoot artifact={artifact} href={href} />
    </div>
  );
}

function IconArtifactTile({
  artifact,
  href,
}: {
  artifact: MessageArtifact;
  href: string;
}) {
  return (
    <div className="artifact-tile">
      <TileHead artifact={artifact} />
      <a
        className="artifact-tile-icon"
        href={href}
        target="_blank"
        rel="noreferrer"
        title={`打开 ${artifact.name}`}
      >
        <FileGlyph />
        <span>{artifactBadge(artifact.name)}</span>
      </a>
      <TileFoot artifact={artifact} href={href} />
    </div>
  );
}

export function ArtifactGrid({
  artifacts,
  agentId,
}: {
  artifacts: MessageArtifact[];
  agentId: string;
}) {
  if (artifacts.length === 0) return null;
  return (
    <div className="message-artifacts">
      <div className="message-artifacts-label">产物</div>
      <div className="artifact-grid" aria-label="产物文件">
        {artifacts.map((artifact) => {
          const href = workspaceFileUrl(agentId, artifact.path) ?? "";
          if (artifact.kind === "image") {
            return (
              <ImageArtifactTile
                key={artifact.path}
                artifact={artifact}
                href={href}
              />
            );
          }
          if (isTextArtifact(artifact.name)) {
            return (
              <TextArtifactTile
                key={artifact.path}
                artifact={artifact}
                agentId={agentId}
                href={href}
              />
            );
          }
          return (
            <IconArtifactTile
              key={artifact.path}
              artifact={artifact}
              href={href}
            />
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 增加样式**

在 `apps/web/src/styles.css` 的 `.message-artifacts .message-file` 规则之后加入：

```css
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.artifact-tile {
  display: flex;
  flex-direction: column;
  aspect-ratio: 1 / 1;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.72);
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.artifact-tile:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

.artifact-tile-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  padding: 7px 9px;
  border-bottom: 1px solid var(--border);
}

.artifact-badge {
  flex: 0 0 auto;
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.03em;
}

.artifact-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-tile-body {
  flex: 1 1 auto;
  min-height: 0;
  padding: 8px 9px;
  overflow: auto;
  overscroll-behavior: contain;
  outline: none;
}

.artifact-tile-body:focus-visible,
.artifact-tile-image:focus-visible,
.artifact-tile-icon:focus-visible,
.artifact-open:focus-visible {
  outline: 2px solid rgb(var(--accent-rgb) / 0.55);
  outline-offset: -2px;
  border-radius: 6px;
}

.artifact-tile-text {
  margin: 0;
  color: var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.artifact-tile-markdown .markdown {
  font-size: 12px;
  line-height: 1.5;
}

.artifact-tile-markdown .markdown h1,
.artifact-tile-markdown .markdown h2,
.artifact-tile-markdown .markdown h3 {
  margin: 0.5em 0 0.35em;
  font-size: 13px;
  line-height: 1.35;
}

.artifact-tile-markdown .markdown p,
.artifact-tile-markdown .markdown ul,
.artifact-tile-markdown .markdown ol {
  margin: 0.35em 0;
}

.artifact-tile-markdown .markdown pre {
  max-width: 100%;
  overflow-x: auto;
}

.artifact-tile-markdown .markdown img {
  max-width: 100%;
  height: auto;
}

.artifact-tile-markdown .markdown table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
}

.artifact-tile-image {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--panel-2);
}

.artifact-tile-image img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.artifact-tile-icon {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex: 1 1 auto;
  min-height: 0;
  color: var(--accent);
  text-decoration: none;
}

.artifact-tile-icon span {
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.artifact-tile-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  padding: 6px 9px;
  border-top: 1px solid var(--border);
  color: var(--faint);
  font-size: 10px;
}

.artifact-size {
  flex: 0 0 auto;
}

.artifact-note {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-open {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--accent);
  text-decoration: none;
  font-size: 11px;
}

.artifact-open:hover {
  text-decoration: underline;
}

.artifact-empty {
  color: var(--faint);
  font-size: 11px;
}

.artifact-skeleton {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.artifact-skeleton i {
  display: block;
  height: 8px;
  border-radius: 4px;
  background: rgba(var(--ink-rgb) / 0.07);
  animation: artifact-skeleton-pulse 1.2s ease-in-out infinite;
}

.artifact-skeleton i:nth-child(2) {
  width: 82%;
}

.artifact-skeleton i:nth-child(3) {
  width: 64%;
}

@keyframes artifact-skeleton-pulse {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 1; }
}

@media (max-width: 860px) {
  .artifact-grid {
    grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .artifact-skeleton i {
    animation: none;
  }
}
```

- [ ] **Step 3: 构建**

```bash
cd apps/web && npm run build
```

Expected: 构建成功（组件暂未被引用）。

- [ ] **Step 4: 提交**

```bash
git add apps/web/src/components/ArtifactGrid.tsx apps/web/src/styles.css apps/web/dist
git commit -m "Add artifact preview tile grid

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: 接入消息列表

**Files:**
- Modify: `apps/web/src/components/MessageList.tsx`

- [ ] **Step 1: 删除旧产物组件**

从 `MessageList.tsx` 删除 `ArtifactStrip` 函数（从 `function ArtifactStrip(` 到它的结束 `}`）。

- [ ] **Step 2: 引入新组件并替换**

在 import 区加入：

```ts
import { ArtifactGrid } from "./ArtifactGrid";
```

把两处：

```tsx
<ArtifactStrip artifacts={message.artifacts ?? []} agentId={agentId} />
<ArtifactStrip artifacts={liveArtifacts} agentId={agentId} />
```

替换为：

```tsx
<ArtifactGrid artifacts={message.artifacts ?? []} agentId={agentId} />
<ArtifactGrid artifacts={liveArtifacts} agentId={agentId} />
```

- [ ] **Step 3: 清理未用 import**

删除 `MessageList.tsx` 中已经不再使用的 `workspaceFileUrl` import（`grep workspaceFileUrl apps/web/src/components/MessageList.tsx` 确认没有其他引用）。保留 `formatBytes`、`isImageName` 等仍被用户附件使用的导入。

- [ ] **Step 4: 构建**

```bash
cd apps/web && npm run build
```

Expected: 构建成功，无未使用导入导致的错误。

- [ ] **Step 5: 手工检查**

在有产物的会话里确认：

- `.md` 方块渲染 Markdown，其他文本等宽滚动。
- 图片方块不裁切。
- PDF/zip 显示图标且 Network 面板没有 `file-preview` 请求。
- 点击「打开」能新标签打开/下载。
- 手机上约两列。

- [ ] **Step 6: 提交**

```bash
git add apps/web/src/components/MessageList.tsx apps/web/dist
git commit -m "Render chat artifacts as preview tiles

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: 文档同步与整体验收

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 `CLAUDE.md`**

在「网页聊天：`POST /api/chat/messages` …」这一条的末尾补充：

```text
回复末尾的「产物」以方块网格展示：文本文件通过 `GET /api/agents/{agent_id}/file-preview/{path}` 读取内容（扩展名白名单、512 KB 截断、UTF-8 解码），`.md` 按 Markdown 渲染、其他文本原样滚动；图片为缩略图，PDF/Office/压缩包显示类型图标；预览按可见方块懒加载并缓存。
```

- [ ] **Step 2: 更新 `README.md` 的 `## Chat` 段落**

保留现有的「Agent 产物：…」条目不动，在它之后追加：

```markdown
- 产物方块：回复末尾的「产物」以方块网格展示。文本文件在方块内展示内容并滚动（`.md` 渲染 Markdown，其他文本等宽原样）；内容由 `GET /api/agents/{agent_id}/file-preview/{path}` 提供，服务端做文本扩展名白名单与 512 KB 截断。图片显示缩略图（不裁切），PDF、Office、压缩包显示类型图标；点击「打开」走现有文件下载接口。预览按可见方块懒加载并缓存，文件删除或读取失败时回落到图标。
```

- [ ] **Step 3: 运行后端全量测试**

```bash
.venv/bin/python -m pytest apps/api/app/tests -q
```

Expected: 全部通过（此前 820 passed, 3 skipped；新增约 11 个用例后约 831 passed）。

- [ ] **Step 4: 构建前端**

```bash
cd apps/web && npm run build
```

Expected: 构建成功；`dist` 有更新时一并提交。

- [ ] **Step 5: 三次自我 code review**

1. 第一遍：逐文件检查预览接口的路径安全、截断、编码与 415/404；前端缓存键、取消与重试。
2. 第二遍：检查与现有产物提取、图片渲染、用户附件 chip、消息虚拟化与 memo 的冲突；方块内部滚动是否影响聊天跟随。
3. 第三遍：检查测试覆盖与文档一致性；发现问题立即修复并重跑测试/构建。

- [ ] **Step 6: 提交**

```bash
git add CLAUDE.md README.md apps/web/dist
git commit -m "Document artifact preview tiles

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

> 推送与生产部署留到最终独立审查之后。

---

## 手工验收清单

- [ ] 一条回复同时产出 `.md`、`.py`、`.json`、`.png`、`.pdf`：Markdown 渲染、等宽文本、文本、图片缩略图、PDF 图标。
- [ ] 文本方块内可滚动看到末尾；超过 512 KB 显示「已截断前 512 KB」。
- [ ] `.md` 里的标题、列表、代码块在方块内正常显示且不溢出。
- [ ] 子目录里的 `.md` 引用同级图片时，方块内图片能正确加载（按文件目录解析）。
- [ ] 图片不变形、不裁切；点击「打开」查看原图。
- [ ] PDF/zip 不发起 `file-preview` 请求，点击「打开」下载。
- [ ] 删除文件后方块显示「无法预览」/图标，聊天不报错。
- [ ] 手机宽度下约两列，方块内滚动不带动整页。
- [ ] 后端全量测试与前端构建通过。
