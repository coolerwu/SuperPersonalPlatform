from __future__ import annotations

import os
import random

import pytest

from app.config import settings
from app.services import knowledge_notes
from app.services.knowledge_notes import (
    DeckState,
    KnowledgeNotesEmpty,
    NoteEntry,
    NoteReadError,
    candidate_order,
    draw_random_note,
    load_deck_state,
    note_title,
    read_note_text,
    reset_index_cache,
    save_deck_state,
    scan_notes,
)


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


def test_load_deck_state_missing_file_starts_fresh(tmp_path):
    state = load_deck_state(tmp_path / "knowledge_deck_missing.json")
    assert state.seen == set()
    assert state.cycle_started_at


def test_candidate_order_reset_cycle_also_defers_exclude():
    entries = _entries("a.md", "b.md")
    order, reset = candidate_order(
        entries, DeckState(seen={"a.md", "b.md"}), "a.md", random.Random(4)
    )
    assert reset is True
    assert order[-1] == "a.md"


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
