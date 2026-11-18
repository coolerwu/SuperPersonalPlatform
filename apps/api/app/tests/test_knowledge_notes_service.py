from __future__ import annotations

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


def test_note_title_ignores_fenced_headings_and_falls_back():
    text = "```\n# 代码里的注释\n```\n# 真标题\n正文"
    assert note_title("a.md", text) == "真标题"
    assert note_title("目录/我的笔记.md", "没有标题") == "我的笔记"


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
