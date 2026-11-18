# 随机笔记展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/knowledge` 页面，每次随机展示一篇知识库笔记原文（洗牌不重复），并支持「追问」一键跳到聊天新会话、把笔记带进引用。

**Architecture:** 后端新增 `services/knowledge_notes.py`：扫描 `knowledge/` 下的 `.md`/`.txt`（60 秒内存索引）、用 `data/knowledge_deck.json` 记录本轮已看集合、按「未看优先，看完重置」的洗牌规则抽取，并由 `routers/knowledge.py` 暴露 `GET /api/knowledge/notes/random`。前端新增懒加载页面 `RandomNotesPage`，复用现有 `Markdown` 渲染、路由/侧栏机制与聊天 quote 预填机制；「追问」纯前端完成，不需要新接口。

**Tech Stack:** FastAPI + pydantic-settings + pytest（asyncio_mode=auto）；React 18 + TypeScript + Vite；无新增依赖。

---

## 文件结构

- Create: `apps/api/app/services/knowledge_notes.py` — 扫描、索引缓存、洗牌状态、抽卡编排
- Modify: `apps/api/app/config.py` — 新增 `knowledge_deck_path` 属性
- Modify: `apps/api/app/routers/knowledge.py` — 新增 `GET /notes/random`
- Create: `apps/api/app/tests/test_knowledge_notes_service.py` — 服务层测试
- Create: `apps/api/app/tests/test_knowledge_notes_api.py` — 路由测试
- Modify: `apps/web/src/types.ts` — `RandomNote` / `RandomNoteMeta` / `RandomNoteResponse`
- Modify: `apps/web/src/api.ts` — `api.randomNote`
- Create: `apps/web/src/noteQuote.ts` — 追问引用文本生成
- Create: `apps/web/src/components/RandomNotesPage.tsx` — 页面组件
- Modify: `apps/web/src/routes.ts` — `knowledge` 视图与 `/knowledge` 路径
- Modify: `apps/web/src/components/Sidebar.tsx` — 「随机笔记」入口与 `knowledgeActive`
- Modify: `apps/web/src/App.tsx` — 懒加载、渲染分支、追问接线
- Modify: `apps/web/src/styles.css` — 页面与卡片样式
- Modify: `docs/intent.md` — 新增 F28
- Modify: `CLAUDE.md` — 页面/侧栏/状态文件说明
- Modify: `README.md` — 页面与 `data/` 清单（如有相关章节）

---

### Task 1: 扫描服务（收集文件、标题、索引缓存）

**Files:**
- Create: `apps/api/app/services/knowledge_notes.py`
- Test: `apps/api/app/tests/test_knowledge_notes_service.py`

- [ ] **Step 1: 写失败的测试**

创建 `apps/api/app/tests/test_knowledge_notes_service.py`：

```python
from __future__ import annotations

import os

import pytest

from app.services import knowledge_notes
from app.services.knowledge_notes import note_title, reset_index_cache, scan_notes


def test_scan_notes_filters_and_reads_titles(tmp_path):
    (tmp_path / "交易").mkdir()
    (tmp_path / "交易" / "背驰.md").write_text("# MACD 与背驰\n正文", encoding="utf-8")
    (tmp_path / "plain.txt").write_text("没有标题的纯文本", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.md").write_text("# 隐藏", encoding="utf-8")
    (tmp_path / ".dot.md").write_text("# 隐藏文件", encoding="utf-8")
    (tmp_path / "big.md").write_bytes(b"a" * (knowledge_notes.MAX_NOTE_BYTES + 1))

    entries = scan_notes(tmp_path)

    assert set(entries) == {"交易/背驰.md", "plain.txt"}
    assert entries["交易/背驰.md"].title == "MACD 与背驰"
    assert entries["交易/背驰.md"].size > 0
    assert entries["plain.txt"].title == "plain"


def test_scan_notes_missing_root_returns_empty(tmp_path):
    assert scan_notes(tmp_path / "nope") == {}


def test_scan_notes_skips_symlinked_notes(tmp_path):
    target = tmp_path / "real.md"
    target.write_text("# 真实", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks are not supported on this platform")

    entries = scan_notes(tmp_path)

    assert set(entries) == {"real.md"}


def test_scan_notes_accepts_uppercase_suffix(tmp_path):
    (tmp_path / "UPPER.MD").write_text("# 大写", encoding="utf-8")

    entries = scan_notes(tmp_path)

    assert set(entries) == {"UPPER.MD"}


def test_scan_notes_size_boundary(tmp_path):
    (tmp_path / "exact.md").write_bytes(b"a" * knowledge_notes.MAX_NOTE_BYTES)
    (tmp_path / "over.md").write_bytes(b"a" * (knowledge_notes.MAX_NOTE_BYTES + 1))

    entries = scan_notes(tmp_path)

    assert set(entries) == {"exact.md"}


def test_note_title_ignores_fenced_headings_and_falls_back():
    text = "```\n# 代码里的注释\n```\n# 真标题\n正文"
    assert note_title("a.md", text) == "真标题"
    assert note_title("目录/我的笔记.md", "没有标题") == "我的笔记"


def test_note_title_skips_frontmatter_and_matches_fences_by_marker():
    frontmatter = "---\ntitle: 元数据\n# 不是标题\n---\n# 真标题 ###\n正文"
    assert note_title("a.md", frontmatter) == "真标题"

    tilde = "~~~\n# 围栏内的注释\n```\n# 仍在围栏内\n~~~\n# 标题"
    assert note_title("b.md", tilde) == "标题"

    backtick = "```\n~~~\n# 围栏内\n```\n# 标题"
    assert note_title("c.md", backtick) == "标题"


@pytest.mark.asyncio
async def test_index_cache_uses_ttl_and_reset(tmp_path):
    reset_index_cache()
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    first = await knowledge_notes.get_note_index(tmp_path)
    assert set(first) == {"a.md"}

    (tmp_path / "b.md").write_text("# B", encoding="utf-8")
    cached = await knowledge_notes.get_note_index(tmp_path)
    assert set(cached) == {"a.md"}

    reset_index_cache()
    refreshed = await knowledge_notes.get_note_index(tmp_path)
    assert set(refreshed) == {"a.md", "b.md"}


@pytest.mark.asyncio
async def test_index_cache_expires_with_ttl(tmp_path):
    reset_index_cache()
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    await knowledge_notes.get_note_index(tmp_path)

    (tmp_path / "b.md").write_text("# B", encoding="utf-8")
    refreshed = await knowledge_notes.get_note_index(tmp_path, ttl=0)

    assert set(refreshed) == {"a.md", "b.md"}


@pytest.mark.asyncio
async def test_missing_root_is_not_cached(tmp_path):
    reset_index_cache()
    root = tmp_path / "knowledge"
    assert await knowledge_notes.get_note_index(root) == {}

    root.mkdir()
    (root / "a.md").write_text("# A", encoding="utf-8")

    assert set(await knowledge_notes.get_note_index(root)) == {"a.md"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: 收集错误/导入失败（`ModuleNotFoundError: No module named 'app.services.knowledge_notes'`）。

- [ ] **Step 3: 实现扫描服务**

创建 `apps/api/app/services/knowledge_notes.py`：

```python
"""Knowledge-base note discovery and cached index for the random notes page."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NOTE_SUFFIXES = frozenset({".md", ".txt"})
MAX_NOTE_BYTES = 2 * 1024 * 1024
TITLE_PREFIX_BYTES = 8192
INDEX_TTL_SECONDS = 60.0

_CLOSING_HASHES = re.compile(r"(?:^|\s+)#+\s*$")


@dataclass(frozen=True)
class NoteEntry:
    path: str  # POSIX path relative to the knowledge root
    title: str
    size: int


def _read_prefix(path: Path, limit: int = TITLE_PREFIX_BYTES) -> str:
    with path.open("rb") as handle:
        raw = handle.read(limit)
    return raw.decode("utf-8-sig", errors="replace")


def note_title(rel_path: str, text: str) -> str:
    lines = text.splitlines()
    start = 0
    if lines and lines[0] == "---":
        for index in range(1, len(lines)):
            if lines[index] == "---":
                start = index + 1
                break
    fence: str | None = None
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("```"):
            if fence is None:
                fence = "```"
            elif fence == "```":
                fence = None
            continue
        if stripped.startswith("~~~"):
            if fence is None:
                fence = "~~~"
            elif fence == "~~~":
                fence = None
            continue
        if fence is not None:
            continue
        if stripped.startswith("# "):
            title = _CLOSING_HASHES.sub("", stripped[2:].strip()).strip()
            if title:
                return title
    stem = Path(rel_path).stem.strip()
    return stem or Path(rel_path).name


def scan_notes(root: Path) -> dict[str, NoteEntry]:
    """Collect readable notes under root keyed by POSIX relative path."""
    entries: dict[str, NoteEntry] = {}
    if not root.is_dir():
        return entries
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            target = Path(dirpath) / name
            if target.suffix.lower() not in NOTE_SUFFIXES:
                continue
            try:
                if target.is_symlink() or not target.is_file():
                    continue
                size = target.stat().st_size
                if size > MAX_NOTE_BYTES:
                    continue
                text = _read_prefix(target)
            except OSError as exc:
                logger.warning("knowledge notes: cannot read %s: %s", target, exc)
                continue
            rel = target.relative_to(root).as_posix()
            entries[rel] = NoteEntry(path=rel, title=note_title(rel, text), size=size)
    return entries


_index_root: Path | None = None
_index_entries: dict[str, NoteEntry] = {}
_index_at = 0.0


def reset_index_cache() -> None:
    """Clear the module-level index cache (test hook)."""
    global _index_root, _index_entries, _index_at
    _index_root = None
    _index_entries = {}
    _index_at = 0.0


async def get_note_index(
    root: Path, *, ttl: float = INDEX_TTL_SECONDS
) -> dict[str, NoteEntry]:
    """Return the cached note index, rescanning in a worker thread when stale."""
    global _index_root, _index_entries, _index_at
    if not root.is_dir():
        # The mirror is briefly renamed aside during resets; never cache that.
        return {}
    if _index_root == root and time.monotonic() - _index_at < ttl:
        return dict(_index_entries)
    entries = await asyncio.to_thread(scan_notes, root)
    _index_root = root
    _index_entries = entries
    _index_at = time.monotonic()
    return dict(entries)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: `10 passed`。

- [ ] **Step 5: 提交**

```bash
git add .
git commit -m "Add knowledge notes scanner service"
```

---

### Task 2: 洗牌状态与候选顺序

**Files:**
- Modify: `apps/api/app/services/knowledge_notes.py`
- Modify: `apps/api/app/config.py`
- Test: `apps/api/app/tests/test_knowledge_notes_service.py`

- [ ] **Step 1: 写失败的测试**

在 `test_knowledge_notes_service.py` 追加（并补 import）：

```python
import random

from app.services.knowledge_notes import (
    DeckState,
    NoteEntry,
    candidate_order,
    load_deck_state,
    save_deck_state,
)


def _entries(*paths: str) -> dict[str, NoteEntry]:
    return {path: NoteEntry(path=path, title=path, size=1) for path in paths}


def test_candidate_order_skips_seen_and_respects_exclude():
    entries = _entries("a.md", "b.md", "c.md")
    order, reset = candidate_order(
        entries, DeckState(seen={"a.md"}), None, random.Random(1)
    )
    assert reset is False
    assert "a.md" not in order
    assert set(order) == {"b.md", "c.md"}

    order, _ = candidate_order(entries, DeckState(seen=set()), "b.md", random.Random(0))
    assert order[-1] == "b.md"
    assert set(order) == {"a.md", "b.md", "c.md"}


def test_candidate_order_resets_when_everything_seen():
    entries = _entries("a.md", "b.md")
    order, reset = candidate_order(
        entries, DeckState(seen={"a.md", "b.md"}), None, random.Random(2)
    )
    assert reset is True
    assert set(order) == {"a.md", "b.md"}


def test_candidate_order_ignores_seen_paths_that_disappeared():
    entries = _entries("a.md", "b.md")
    order, reset = candidate_order(
        entries, DeckState(seen={"gone.md", "a.md"}), None, random.Random(3)
    )
    assert reset is False
    assert order == ["b.md"]


def test_deck_state_roundtrip_and_corruption(tmp_path):
    path = tmp_path / "knowledge_deck.json"
    state = DeckState(cycle_started_at="2026-11-18T00:00:00+00:00", seen={"a.md"})
    save_deck_state(path, state)
    loaded = load_deck_state(path)
    assert loaded.seen == {"a.md"}
    assert loaded.cycle_started_at == "2026-11-18T00:00:00+00:00"

    path.write_text("{not json", encoding="utf-8")
    fresh = load_deck_state(path)
    assert fresh.seen == set()
    assert fresh.cycle_started_at
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: ImportError（`DeckState` 等不存在）。

- [ ] **Step 3: 实现洗牌状态与候选顺序**

在 `knowledge_notes.py` 顶部 import 增加：

```python
import json
import random
from datetime import datetime, timezone
```

在文件末尾追加：

```python
CYCLE_RESET_NOTICE = "本轮已看完，重新洗牌"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeckState:
    cycle_started_at: str = ""
    seen: set[str] = field(default_factory=set)


def load_deck_state(path: Path) -> DeckState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DeckState(cycle_started_at=_utcnow())
    except (OSError, ValueError):
        logger.warning("knowledge notes: resetting unreadable deck state %s", path)
        return DeckState(cycle_started_at=_utcnow())
    if not isinstance(payload, dict):
        logger.warning("knowledge notes: resetting malformed deck state %s", path)
        return DeckState(cycle_started_at=_utcnow())
    seen_raw = payload.get("seen")
    seen = (
        {item for item in seen_raw if isinstance(item, str)}
        if isinstance(seen_raw, list)
        else set()
    )
    cycle = payload.get("cycle_started_at")
    return DeckState(
        cycle_started_at=cycle if isinstance(cycle, str) and cycle else _utcnow(),
        seen=seen,
    )


def save_deck_state(path: Path, state: DeckState) -> None:
    payload = json.dumps(
        {"cycle_started_at": state.cycle_started_at, "seen": sorted(state.seen)},
        ensure_ascii=False,
        indent=2,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        logger.warning("knowledge notes: cannot save deck state %s: %s", path, exc)


def candidate_order(
    entries: dict[str, NoteEntry],
    state: DeckState,
    exclude: str | None,
    rng: random.Random | None = None,
) -> tuple[list[str], bool]:
    """Ordered candidates; second value is True when a new cycle starts."""
    picker = rng or random.Random()
    known = set(entries)
    unseen = sorted(known - (state.seen & known))
    if unseen:
        picker.shuffle(unseen)
        candidates, reset = unseen, False
    else:
        candidates = sorted(known)
        picker.shuffle(candidates)
        reset = True
    if exclude in candidates and len(candidates) > 1:
        candidates.remove(exclude)
        candidates.append(exclude)
    return candidates, reset
```

同时把 dataclass import 改为 `from dataclasses import dataclass, field`。

在 `apps/api/app/config.py` 的 `knowledge_state_path` 属性后面加：

```python
    @property
    def knowledge_deck_path(self) -> Path:
        return self.app_data_dir / "knowledge_deck.json"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: `8 passed`。

- [ ] **Step 5: 提交**

```bash
git add .
git commit -m "Add shuffle deck state for random notes"
```

---

### Task 3: 正文读取与抽卡编排

**Files:**
- Modify: `apps/api/app/services/knowledge_notes.py`
- Test: `apps/api/app/tests/test_knowledge_notes_service.py`

- [ ] **Step 1: 写失败的测试**

在 `test_knowledge_notes_service.py` 追加：

```python
from app.config import settings
from app.services.knowledge_notes import (
    KnowledgeNotesEmpty,
    NoteReadError,
    draw_random_note,
    read_note_text,
)


def _prepare_knowledge(tmp_path, monkeypatch):
    reset_index_cache()
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(settings, "app_knowledge_dir", root)
    monkeypatch.setattr(settings, "app_data_dir", tmp_path / "data")
    return root


def test_read_note_text_rejects_traversal(tmp_path):
    (tmp_path / "ok.md").write_text("内容", encoding="utf-8")
    assert read_note_text(tmp_path, "ok.md") == "内容"
    with pytest.raises(NoteReadError):
        read_note_text(tmp_path, "../outside.md")


def test_read_note_text_replaces_invalid_utf8(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfehello")
    assert read_note_text(tmp_path, "bad.txt") == "��hello"


@pytest.mark.asyncio
async def test_draw_random_note_consumes_deck(tmp_path, monkeypatch):
    root = _prepare_knowledge(tmp_path, monkeypatch)
    (root / "a.md").write_text("# A\n内容 A", encoding="utf-8")
    (root / "b.md").write_text("# B\n内容 B", encoding="utf-8")

    first, content, meta = await draw_random_note(None)
    assert first.path in {"a.md", "b.md"}
    label = "A" if first.path == "a.md" else "B"
    assert content == f"# {label}\n内容 {label}"
    assert meta == {"seen": 1, "total": 2, "notice": ""}

    second, _, meta2 = await draw_random_note(first.path)
    assert second.path != first.path
    assert meta2["seen"] == 2
    assert meta2["notice"] == ""

    third, _, meta3 = await draw_random_note(second.path)
    assert third.path != second.path
    assert meta3["notice"] == "本轮已看完，重新洗牌"
    assert meta3["seen"] == 1
    assert meta3["total"] == 2


@pytest.mark.asyncio
async def test_draw_random_note_empty_knowledge(tmp_path, monkeypatch):
    _prepare_knowledge(tmp_path, monkeypatch)
    with pytest.raises(KnowledgeNotesEmpty):
        await draw_random_note(None)


@pytest.mark.asyncio
async def test_draw_random_note_skips_unreadable(tmp_path, monkeypatch):
    root = _prepare_knowledge(tmp_path, monkeypatch)
    (root / "a.md").write_text("# A\n内容 A", encoding="utf-8")
    (root / "b.md").write_text("# B\n内容 B", encoding="utf-8")

    original = knowledge_notes.read_note_text

    def fake_read(root_path, rel_path):
        if rel_path == "a.md":
            raise NoteReadError("boom")
        return original(root_path, rel_path)

    monkeypatch.setattr(knowledge_notes, "read_note_text", fake_read)
    note, _, meta = await draw_random_note(None)
    assert note.path == "b.md"
    assert meta["seen"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: ImportError（`read_note_text` 等不存在）。

- [ ] **Step 3: 实现读取与抽卡编排**

在 `knowledge_notes.py` 顶部 import 增加：

```python
from app.config import settings
```

在文件末尾追加：

```python
class KnowledgeNotesEmpty(Exception):
    """No readable notes under the knowledge directory."""


class NoteReadError(Exception):
    """A note could not be read safely."""


def read_note_text(root: Path, rel_path: str) -> str:
    root_resolved = root.resolve()
    target = (root / rel_path).resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise NoteReadError(f"path escapes knowledge dir: {rel_path}")
    if not target.is_file():
        raise NoteReadError(f"note is not a readable file: {rel_path}")
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise NoteReadError(f"cannot read note: {rel_path}") from exc


_deck_lock = asyncio.Lock()


async def draw_random_note(
    exclude: str | None,
) -> tuple[NoteEntry, str, dict[str, object]]:
    root = settings.knowledge_dir
    entries = await get_note_index(root)
    if not entries:
        raise KnowledgeNotesEmpty()
    state_path = settings.knowledge_deck_path
    async with _deck_lock:
        state = load_deck_state(state_path)
        candidates, cycle_reset = candidate_order(entries, state, exclude)
        last_error: Exception | None = None
        for rel in candidates:
            try:
                content = read_note_text(root, rel)
            except NoteReadError as exc:
                last_error = exc
                logger.warning("knowledge notes: skipping unreadable note %s", rel)
                continue
            if cycle_reset:
                state = DeckState(cycle_started_at=_utcnow(), seen={rel})
            else:
                state.seen.add(rel)
            save_deck_state(state_path, state)
            known = set(entries)
            meta: dict[str, object] = {
                "seen": len(state.seen & known),
                "total": len(known),
                "notice": CYCLE_RESET_NOTICE if cycle_reset else "",
            }
            return entries[rel], content, meta
        raise NoteReadError("读取笔记失败") from last_error
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_service.py -q`
Expected: `13 passed`。

- [ ] **Step 5: 提交**

```bash
git add .
git commit -m "Draw random knowledge notes with deck state"
```

---

### Task 4: 随机笔记接口

**Files:**
- Modify: `apps/api/app/routers/knowledge.py`
- Test: `apps/api/app/tests/test_knowledge_notes_api.py`

- [ ] **Step 1: 写失败的测试**

创建 `apps/api/app/tests/test_knowledge_notes_api.py`：

```python
from __future__ import annotations

import pytest

from app.config import settings
from app.services.knowledge_notes import reset_index_cache


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(settings, "app_knowledge_dir", root)
    monkeypatch.setattr(settings, "app_data_dir", tmp_path / "data")
    reset_index_cache()
    yield root
    reset_index_cache()


def test_random_note_returns_content(client, notes_dir):
    (notes_dir / "note.md").write_text("# 标题\n正文", encoding="utf-8")
    response = client.get("/api/knowledge/notes/random")
    assert response.status_code == 200
    body = response.json()
    assert body["note"] == {
        "path": "note.md",
        "title": "标题",
        "content": "# 标题\n正文",
        "size": len("# 标题\n正文".encode("utf-8")),
    }
    assert body["meta"] == {"seen": 1, "total": 1, "notice": ""}


def test_random_note_avoids_exclude(client, notes_dir):
    (notes_dir / "a.md").write_text("# A", encoding="utf-8")
    (notes_dir / "b.md").write_text("# B", encoding="utf-8")
    first = client.get("/api/knowledge/notes/random").json()["note"]["path"]
    second = client.get(
        "/api/knowledge/notes/random", params={"exclude": first}
    ).json()["note"]["path"]
    assert {first, second} == {"a.md", "b.md"}


def test_random_note_empty_knowledge(client, notes_dir):
    response = client.get("/api/knowledge/notes/random")
    assert response.status_code == 404
    assert "知识库没有可用的笔记" in response.json()["detail"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_api.py -q`
Expected: FAIL（`404 != 200` / 路由不存在）。

- [ ] **Step 3: 实现接口**

在 `apps/api/app/routers/knowledge.py` 的 import 区增加：

```python
from app.services.knowledge_notes import (
    KnowledgeNotesEmpty,
    NoteReadError,
    draw_random_note,
)
```

在文件末尾追加：

```python
@router.get("/notes/random")
async def random_note(exclude: str = "") -> dict[str, object]:
    try:
        entry, content, meta = await draw_random_note(exclude.strip() or None)
    except KnowledgeNotesEmpty as exc:
        raise HTTPException(
            status_code=404,
            detail="知识库没有可用的笔记，先去配置页检查知识库同步",
        ) from exc
    except NoteReadError as exc:
        raise HTTPException(status_code=500, detail="读取笔记失败") from exc
    return {
        "note": {
            "path": entry.path,
            "title": entry.title,
            "content": content,
            "size": entry.size,
        },
        "meta": meta,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_knowledge_notes_api.py -q`
Expected: `3 passed`。

再跑一次全量后端测试：
Run: `.venv/bin/python -m pytest apps/api/app/tests -q`
Expected: 全部通过（基线 974 passed, 3 skipped，新增 16 个用例后约 990 passed）。

- [ ] **Step 5: 提交**

```bash
git add .
git commit -m "Expose random knowledge note endpoint"
```

---

### Task 5: 前端类型与 API 客户端

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`

- [ ] **Step 1: 加类型**

在 `apps/web/src/types.ts` 的 `KnowledgeState` 定义后面追加：

```ts
export type RandomNote = {
  path: string;
  title: string;
  content: string;
  size: number;
};

export type RandomNoteMeta = {
  seen: number;
  total: number;
  notice: string;
};

export type RandomNoteResponse = {
  note: RandomNote;
  meta: RandomNoteMeta;
};
```

- [ ] **Step 2: 加 API 方法**

在 `apps/web/src/api.ts` 顶部类型 import 里加 `RandomNoteResponse`（按字母序放在 `Provider` 后面即可），并在 `getKnowledge` 附近追加：

```ts
  randomNote: (exclude?: string) =>
    request<RandomNoteResponse>(
      `/api/knowledge/notes/random${
        exclude ? `?exclude=${encodeURIComponent(exclude)}` : ""
      }`,
    ),
```

- [ ] **Step 3: 构建验证**

Run: `cd apps/web && npm run build`
Expected: `tsc --noEmit` 与 vite build 通过。

- [ ] **Step 4: 提交**

```bash
git add .
git commit -m "Add random note API types and client"
```

---

### Task 6: 追问引用 helper、页面组件与样式

**Files:**
- Create: `apps/web/src/noteQuote.ts`
- Create: `apps/web/src/components/RandomNotesPage.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 写引用文本 helper**

创建 `apps/web/src/noteQuote.ts`：

```ts
import type { RandomNote } from "./types";

export const NOTE_QUOTE_LIMIT = 6000;
export const KNOWLEDGE_DIR_LABEL = "~/.super-personal-platform/knowledge";

/** Quote block handed to the composer when asking the Agent about a note. */
export function noteQuote(note: RandomNote): string {
  const truncated = note.content.length > NOTE_QUOTE_LIMIT;
  const body = truncated
    ? `${note.content.slice(0, NOTE_QUOTE_LIMIT)}\n（已截断，完整内容见上方路径）`
    : note.content;
  return `笔记：${note.path}（完整原文：${KNOWLEDGE_DIR_LABEL}/${note.path}）\n\n${body}`;
}
```

- [ ] **Step 2: 写页面组件**

创建 `apps/web/src/components/RandomNotesPage.tsx`：

```tsx
import { useCallback, useEffect, useState } from "react";

import { ApiError, api, errorText } from "../api";
import { formatBytes } from "../messageContent";
import { Markdown } from "./Markdown";
import type { RandomNote, RandomNoteMeta } from "../types";

type RandomNotesPageProps = {
  onAsk: (note: RandomNote) => void;
  onClose: () => void;
  onOpenKnowledgeSettings: () => void;
};

export function RandomNotesPage({
  onAsk,
  onClose,
  onOpenKnowledgeSettings,
}: RandomNotesPageProps) {
  const [note, setNote] = useState<RandomNote | null>(null);
  const [meta, setMeta] = useState<RandomNoteMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [empty, setEmpty] = useState(false);

  const load = useCallback(async (exclude?: string) => {
    setLoading(true);
    setError("");
    try {
      const response = await api.randomNote(exclude);
      setNote(response.note);
      setMeta(response.meta);
      setEmpty(false);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) {
        setEmpty(true);
        setNote(null);
        setMeta(null);
      } else {
        setError(errorText(exc) || "加载失败，请重试");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="knowledge-page">
      <header className="settings-header">
        <div>
          <div className="eyebrow">KNOWLEDGE</div>
          <h1>随机笔记</h1>
          <p>从知识库里随机翻一篇笔记，看完换下一篇。</p>
        </div>
        <button className="ghost-button" onClick={onClose}>
          返回对话
        </button>
      </header>
      <div className="settings-scroll">
        <div className="knowledge-progress" role="status">
          {meta ? (
            <span>
              本轮已看 <b>{meta.seen}</b> / 共 <b>{meta.total}</b> · 剩余{" "}
              {Math.max(meta.total - meta.seen, 0)}
            </span>
          ) : (
            <span>正在整理笔记列表…</span>
          )}
          {meta?.notice ? <em className="knowledge-notice">{meta.notice}</em> : null}
        </div>

        {empty ? (
          <div className="knowledge-empty">
            <p>知识库没有可用的笔记，先去配置页检查知识库同步。</p>
            <button className="primary" onClick={onOpenKnowledgeSettings}>
              去配置知识库
            </button>
          </div>
        ) : error ? (
          <div className="knowledge-empty">
            <p>{error}</p>
            <button className="primary" onClick={() => void load(note?.path)}>
              重试
            </button>
          </div>
        ) : (
          <section className="knowledge-card">
            {note ? (
              <>
                <header className="knowledge-card-head">
                  <div>
                    <h2>{note.title}</h2>
                    <div className="knowledge-card-path">📄 {note.path}</div>
                  </div>
                  <span className="knowledge-card-size">{formatBytes(note.size)}</span>
                </header>
                <div className="knowledge-card-body">
                  <Markdown content={note.content} />
                </div>
              </>
            ) : (
              <div className="knowledge-card-skeleton">正在抽下一篇…</div>
            )}
            <footer className="knowledge-card-actions">
              <button
                className="primary"
                disabled={loading}
                onClick={() => void load(note?.path)}
              >
                {loading ? "正在抽下一篇…" : "换一篇"}
              </button>
              <button
                className="ghost-button"
                disabled={loading || !note}
                onClick={() => {
                  if (note) onAsk(note);
                }}
              >
                ✦ 追问
              </button>
            </footer>
          </section>
        )}
      </div>
    </main>
  );
}
```

- [ ] **Step 3: 加样式**

在 `apps/web/src/styles.css` 里，把 `.knowledge-page` 加进共享的页面 flex 规则（`settings-page, memory-page, ... .backtest-page` 那一组，行尾追加 `.knowledge-page,`），并在该文件末尾追加：

```css
.knowledge-progress {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 14px;
  color: var(--muted);
  font-size: 13px;
}

.knowledge-progress b {
  color: var(--ink);
}

.knowledge-notice {
  color: #2f5f8f;
  font-style: normal;
}

.knowledge-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 20px;
  border: 1px solid rgba(160, 200, 255, 0.35);
  background: linear-gradient(
    145deg,
    rgba(255, 255, 255, 0.9),
    rgba(240, 246, 255, 0.93)
  );
  box-shadow: 0 18px 40px rgba(30, 60, 110, 0.12);
}

.knowledge-card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  padding: 18px 22px 12px;
  border-bottom: 1px solid rgba(140, 170, 210, 0.25);
}

.knowledge-card-head h2 {
  margin: 0;
  font-size: 18px;
}

.knowledge-card-path,
.knowledge-card-size {
  color: var(--muted);
  font-size: 11px;
}

.knowledge-card-path {
  margin-top: 4px;
}

.knowledge-card-body {
  max-height: 60vh;
  overflow-y: auto;
  padding: 16px 22px;
}

.knowledge-card-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  padding: 14px 22px 18px;
}

.knowledge-card-skeleton,
.knowledge-empty {
  padding: 48px 22px;
  text-align: center;
  color: var(--muted);
}

.knowledge-empty .primary {
  margin-top: 12px;
}

@media (max-width: 860px) {
  .knowledge-card-body {
    max-height: 52vh;
  }
}
```

- [ ] **Step 4: 构建验证**

Run: `cd apps/web && npm run build`
Expected: 通过（此时页面组件尚未被引用，打包可省略；若 tsc 报「未使用」不成立——组件是导出文件，不会被 tree-shaking 报错）。

- [ ] **Step 5: 提交**

```bash
git add .
git commit -m "Add random notes page component and quotes"
```

---

### Task 7: 路由、侧栏与 App 接线（含追问流程）

**Files:**
- Modify: `apps/web/src/routes.ts`
- Modify: `apps/web/src/components/Sidebar.tsx`
- Modify: `apps/web/src/App.tsx`

- [ ] **Step 1: 加路由**

`apps/web/src/routes.ts`：

- `View` 联合类型加 `"knowledge"`（放在 `"memory"` 后面）；
- `VIEW_PATHS` 加 `knowledge: "/knowledge"`。

预期的两处改动形态：

```ts
export type View =
  | "chat"
  | "settings"
  | "memory"
  | "knowledge"
  | "skills"
  // …其余不变
```

```ts
const VIEW_PATHS: Record<View, string> = {
  // …
  memory: "/memory",
  knowledge: "/knowledge",
  skills: "/skills",
  // …
};
```

- [ ] **Step 2: 侧栏加入口**

`apps/web/src/components/Sidebar.tsx`：

- `SidebarProps` 在 `onOpenMemory` 后加 `onOpenKnowledge: () => void;`，在 `memoryActive` 后加 `knowledgeActive: boolean;`；
- `capabilitiesActive` 计算里加 `|| props.knowledgeActive`；
- 在「记忆」按钮之后追加：

```tsx
                <button
                  className={`sidebar-action ${props.knowledgeActive ? "active" : ""}`}
                  onClick={props.onOpenKnowledge}
                >
                  <span className="sidebar-action-icon">
                    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M6 3h9l3 3v15H6z" />
                      <path d="M9 8h6" />
                      <path d="M9 12h6" />
                      <path d="M9 16h4" />
                    </svg>
                  </span>
                  <span>随机笔记</span>
                </button>
```

- [ ] **Step 3: App 接线**

`apps/web/src/App.tsx`：

1. 顶部 import 增加：

```ts
import { noteQuote } from "./noteQuote";
```

并在类型 import 里加 `RandomNote`（与其他类型 import 合并）。

2. `PAGE_LOADERS` 增加：

```ts
  knowledge: () =>
    import("./components/RandomNotesPage").then((module) => ({
      default: module.RandomNotesPage,
    })),
```

3. lazy 声明区增加：

```ts
const RandomNotesPage = lazy(PAGE_LOADERS.knowledge);
```

4. 在 `newSession()` 函数附近新增：

```ts
  function askAboutNote(note: RandomNote) {
    if (!agentId) return;
    newSession();
    const key = sessionKey(agentId, "");
    setQuotesByKey((current) => ({ ...current, [key]: noteQuote(note) }));
    setDraftFor(key, "");
    setFocusSignal((current) => current + 1);
  }
```

5. Sidebar props 里加：

```tsx
        knowledgeActive={view === "knowledge"}
        onOpenKnowledge={() => openView("knowledge")}
```

6. Suspense 内渲染分支：在 `view === "memory" ? (...) :` 之后插入：

```tsx
      ) : view === "knowledge" ? (
        <RandomNotesPage
          onAsk={askAboutNote}
          onClose={() => openView("chat")}
          onOpenKnowledgeSettings={() => {
            setSettingsTab("knowledge");
            openView("settings");
          }}
        />
```

- [ ] **Step 4: 构建验证**

Run: `cd apps/web && npm run build`
Expected: tsc 与 vite 构建通过。

- [ ] **Step 5: 浏览器手动验收**

用本地开发服务器（`cd apps/web && npm run dev`，或 `preview_start`）验证：

1. 在本地知识库目录 `~/.super-personal-platform/knowledge/` 放两三个 `.md` 文件；
2. 打开 `/knowledge`：显示一篇笔记（标题、路径、大小、Markdown 正文）；
3. 点「换一篇」：换成另一篇，顶部进度 +1；
4. 把两篇都看完再换：出现「本轮已看完，重新洗牌」；
5. 点「追问」：切到聊天页的新会话，输入框里有引用块（笔记路径 + 正文），写个问题能发送；
6. 清空知识库目录后刷新页面：显示空态与「去配置知识库」按钮，点击进入配置页知识库分区；
7. 缩窄窗口到 ≤860px：卡片与按钮不溢出。

- [ ] **Step 6: 提交**

```bash
git add .
git commit -m "Wire random notes page into routes and sidebar"
```

---

### Task 8: 文档同步与整体验收

**Files:**
- Modify: `docs/intent.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 补 `docs/intent.md` F28**

在 F27 之后追加：

```markdown
### F28 随机笔记展示

- 状态：现行
- 日期：2026-11-18
- 来源：2026-11-18 随机笔记展示设计（`docs/superpowers/specs/2026-11-18-random-knowledge-notes-design.md`）

#### 功能点

1. 侧栏「Agent 能力」新增「随机笔记」入口，页面 `/knowledge` 每次随机展示 `knowledge/` 下的一篇 `.md`/`.txt` 笔记原文，按 Markdown 渲染、卡片内滚动；顶部显示「本轮已看 N / 共 M · 剩余 R」。
2. 洗牌不重复：本轮看过的笔记不再出现，全库看完一轮后自动重置并提示「本轮已看完，重新洗牌」；进度存 `data/knowledge_deck.json`（原子写，缺失或损坏按新一轮处理）。
3. 扫描跳过隐藏目录/文件、符号链接与超过 2 MiB 的文件；标题取首个 `#` 级标题，缺省用文件名；索引缓存 60 秒。
4. 接口 `GET /api/knowledge/notes/random?exclude=<路径>` 返回 `{note: {path, title, content, size}, meta: {seen, total, notice}}`；知识库为空 404，读取失败 500。
5. 卡片「追问」切到聊天页并新建会话，输入框预置引用：笔记相对路径、完整原文路径（`~/.super-personal-platform/knowledge/<路径>`）与正文前 6000 字（超出注明已截断）。纯前端实现，不调用模型，不需要 Provider。

#### 验收标准

- 打开 `/knowledge` 能看到笔记原文；「换一篇」不重复，本轮看完出现重新洗牌提示与进度重置。
- 知识库为空时显示引导并能跳转配置页知识库分区。
- 「追问」进入新会话并带引用；引用含路径与正文（超 6000 字截断）。
- 不新增数据表、不新增依赖；后端测试与前端构建通过。
```

- [ ] **Step 2: 更新 `CLAUDE.md`**

- 「当前架构」里新增一条（放在知识库条目之后）：

```markdown
- 随机笔记：`/knowledge` 页面每次随机展示 `knowledge/` 下一整篇 `.md`/`.txt` 笔记（Markdown 渲染、卡片内滚动），洗牌不重复、本轮看完自动重置；进度存 `data/knowledge_deck.json`（原子写），索引 60 秒缓存；`GET /api/knowledge/notes/random`；卡片「追问」切到聊天新会话并预置引用（路径 + 正文前 6000 字），纯前端、不调模型。
```

- 「UI 沿用…」路由行把 `/knowledge` 加进页面列表；
- 侧栏描述里「Agent 能力」菜单列表加「随机笔记」；
- 数据表数量不变（仍为五张），无需改动。

- [ ] **Step 3: 更新 `README.md`**

在页面清单/功能列表处补「随机笔记」一段（如无对应章节则跳过）；在 `data/` 文件清单里加 `knowledge_deck.json`。

- [ ] **Step 4: 全量检查**

```bash
.venv/bin/python -m pytest apps/api/app/tests -q
cd apps/web && npm run build
```

Expected: 后端全部通过（基线 974 + 新增 16 个用例），前端构建通过。

- [ ] **Step 5: 提交并推送**

```bash
git add .
git commit -m "Document random notes feature"
git push origin HEAD
```

部署只在用户明确要求时进行（按 project-commit 技能执行）。

---

## 自查记录

- 规格覆盖：扫描（Task 1）、洗牌状态与重置（Task 2/3）、正文读取与安全（Task 3）、接口与错误码（Task 4）、进度统计（Task 3/4）、前端页面与进度/空态/错误态（Task 6）、路由与侧栏（Task 7）、追问引用（Task 6/7）、文档同步（Task 8）、测试与构建（各 Task）。
- 名称一致性：`NoteEntry.path/title/size`、`DeckState.cycle_started_at/seen`、`draw_random_note(exclude)`、`RandomNote{path,title,content,size}`、`RandomNoteMeta{seen,total,notice}`、`api.randomNote(exclude?)` 全文一致。
- 已知取舍：2 MiB 上限、6000 字引用截断、进度为服务端单轮状态（多浏览器共享），均已在规格中声明。
