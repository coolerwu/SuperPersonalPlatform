from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


class ContextKnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class ContextSearchHit:
    path: str
    score: float
    snippet: str


class ContextKnowledgeService:
    max_file_bytes = 500_000
    max_write_bytes = 500_000
    allowed_suffixes = {".md", ".txt", ".json", ".jsonl"}

    def __init__(self, context_workspace: Path) -> None:
        self._context_workspace = context_workspace.resolve()
        self._files_dir = self._context_workspace / "knowledge" / "files"

    @property
    def files_dir(self) -> Path:
        return self._files_dir

    def search(self, query: str, *, top_k: int = 5) -> tuple[ContextSearchHit, ...]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ContextKnowledgeError("query is required")
        limit = min(max(int(top_k or 5), 1), 10)
        documents = self._documents()
        if not documents:
            return ()

        query_terms = _tokenize(normalized_query)
        if not query_terms:
            raise ContextKnowledgeError("query must contain searchable text")
        doc_terms = [_tokenize(text) for _, text in documents]
        doc_freq: dict[str, int] = {}
        for terms in doc_terms:
            for term in set(terms):
                doc_freq[term] = doc_freq.get(term, 0) + 1

        average_doc_length = sum(len(terms) for terms in doc_terms) / max(len(doc_terms), 1)
        hits: list[ContextSearchHit] = []
        for (path, text), terms in zip(documents, doc_terms, strict=True):
            score = _bm25_score(query_terms, terms, doc_freq, len(documents), average_doc_length)
            if normalized_query.lower() in text.lower():
                score += 2.0
            if score <= 0:
                continue
            hits.append(
                ContextSearchHit(
                    path=f"/files/{path.as_posix()}",
                    score=round(score, 4),
                    snippet=_snippet(text, query_terms),
                )
            )
        return tuple(sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit])

    def write(self, *, type: str, absolute_path: str, content: str, mode: str = "append") -> dict[str, object]:
        if type.strip() != "knowledge":
            raise ContextKnowledgeError("type must be knowledge")
        relative_path = self._normalize_tool_path(absolute_path)
        write_mode = str(mode or "append").strip().lower()
        if write_mode not in {"append", "overwrite", "create"}:
            raise ContextKnowledgeError("mode must be append, overwrite, or create")
        if not content:
            raise ContextKnowledgeError("content is required")
        if len(content.encode("utf-8")) > self.max_write_bytes:
            raise ContextKnowledgeError("content is too large")

        target = (self._files_dir / relative_path).resolve()
        if self._files_dir.resolve() not in target.parents:
            raise ContextKnowledgeError("absolute_path is outside context knowledge files")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() not in self.allowed_suffixes:
            raise ContextKnowledgeError("absolute_path suffix must be .md, .txt, .json, or .jsonl")
        if write_mode == "create" and target.exists():
            raise ContextKnowledgeError("file already exists")

        if write_mode == "append" and target.exists():
            existing = target.read_text(encoding="utf-8")
            separator = "" if existing.endswith("\n") or not existing else "\n"
            next_content = existing + separator + content
        else:
            next_content = content
        tmp_path = target.with_name(f".{target.name}.tmp")
        tmp_path.write_text(next_content, encoding="utf-8")
        tmp_path.replace(target)
        return {
            "type": "knowledge",
            "path": f"/files/{relative_path.as_posix()}",
            "mode": write_mode,
            "bytes": len(next_content.encode("utf-8")),
        }

    def _documents(self) -> list[tuple[Path, str]]:
        if not self._files_dir.exists():
            return []
        documents: list[tuple[Path, str]] = []
        for path in sorted(self._files_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self.allowed_suffixes:
                continue
            if path.stat().st_size > self.max_file_bytes:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            chunks = _chunks(content)
            for index, chunk in enumerate(chunks):
                chunk_path = path.relative_to(self._files_dir)
                if len(chunks) > 1:
                    chunk_path = Path(f"{chunk_path.as_posix()}#chunk-{index + 1}")
                documents.append((chunk_path, chunk))
        return documents

    def _normalize_tool_path(self, absolute_path: str) -> Path:
        value = str(absolute_path or "").strip()
        if not value.startswith("/files/"):
            raise ContextKnowledgeError("absolute_path must start with /files/")
        relative = Path(value.removeprefix("/files/"))
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ContextKnowledgeError("absolute_path must not contain . or ..")
        return relative


def _chunks(content: str, *, max_chars: int = 2400) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs or [content.strip()]:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    return [word for word in words if word.strip()]


def _bm25_score(
    query_terms: list[str],
    doc_terms: list[str],
    doc_freq: dict[str, int],
    doc_count: int,
    average_doc_length: float,
) -> float:
    if not doc_terms:
        return 0.0
    term_counts: dict[str, int] = {}
    for term in doc_terms:
        term_counts[term] = term_counts.get(term, 0) + 1
    score = 0.0
    k1 = 1.5
    b = 0.75
    average_length = max(average_doc_length, 1.0)
    doc_length = len(doc_terms)
    for term in query_terms:
        frequency = term_counts.get(term, 0)
        if frequency == 0:
            continue
        df = doc_freq.get(term, 0)
        idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
        denominator = frequency + k1 * (1 - b + b * doc_length / average_length)
        score += idf * frequency * (k1 + 1) / denominator
    return score


def _snippet(text: str, query_terms: list[str], *, radius: int = 140) -> str:
    lowered = text.lower()
    positions = [lowered.find(term) for term in query_terms if lowered.find(term) >= 0]
    start = max(min(positions) - radius, 0) if positions else 0
    end = min(start + radius * 2, len(text))
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    if start:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet
