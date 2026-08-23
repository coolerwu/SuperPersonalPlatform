from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from server.app.context_knowledge_service import ContextKnowledgeService
from server.app.webdav_context_service import WebDAVContextService, run_async
from server.domain.tooling import get_tool_definition
from server.infrastructure.browser_tools import build_browser_extract_tool
from server.infrastructure.config import load_settings


def build_platform_tools(tool_ids: tuple[str, ...], *, context_workspace: Path) -> list[Any]:
    tools = []
    service = ContextKnowledgeService(context_workspace)
    webdav_service = _webdav_context_service(context_workspace)
    for tool_id in tool_ids:
        definition = get_tool_definition(tool_id)
        if definition.id == "search_context":
            tools.append(_search_context_tool(service, webdav_service))
        elif definition.id == "write_context":
            tools.append(_write_context_tool(service, webdav_service))
        elif definition.id == "browser_extract":
            tools.append(build_browser_extract_tool())
    return tools


def _webdav_context_service(context_workspace: Path) -> WebDAVContextService | None:
    workspace = context_workspace.parent
    try:
        settings = load_settings(workspace / "config.yaml")
    except Exception:
        return None
    if not settings.nutstore.enabled or not settings.context.webdav_sync.enabled:
        return None
    if not settings.context.webdav_permissions:
        return None
    return WebDAVContextService(
        workspace=workspace,
        nutstore=settings.nutstore,
        context=settings.context,
    )


def _search_context_tool(service: ContextKnowledgeService, webdav_service: WebDAVContextService | None) -> Any:
    from langchain_core.tools import StructuredTool

    def search_context(query: str, top_k: int = 5) -> str:
        """Search local and synced WebDAV knowledge context.

        Use this to retrieve relevant notes from workspace/context/knowledge/files and readable WebDAV roots before answering.
        For "recent notes" questions, this also returns recent_documents sorted by WebDAV modified time.
        """
        extra_documents: tuple[tuple[str, str], ...] = ()
        recent_documents: list[dict[str, Any]] = []
        sync_error = ""
        limit = min(max(int(top_k or 5), 1), 10)
        if webdav_service is not None:
            try:
                run_async(webdav_service.refresh_if_stale())
                extra_documents = tuple((item.path, item.content) for item in webdav_service.documents())
                if _is_recent_context_query(query):
                    recent_documents = [
                        {
                            "path": item.path,
                            "modified": item.modified,
                            "size": item.size,
                            "snippet": item.snippet,
                        }
                        for item in webdav_service.recent_documents(limit=limit)
                    ]
            except Exception as exc:  # noqa: BLE001
                sync_error = f"{exc.__class__.__name__}: {exc}"
        hits = service.search(query, top_k=limit, extra_documents=extra_documents)
        if not hits:
            payload: dict[str, Any] = {"hits": [], "message": "No matching context knowledge found."}
            if recent_documents:
                payload["recent_documents"] = recent_documents
            if sync_error:
                payload["webdav_sync_error"] = sync_error
            return json.dumps(payload, ensure_ascii=False)
        payload = {
            "hits": [
                {"path": hit.path, "score": hit.score, "snippet": hit.snippet}
                for hit in hits
            ]
        }
        if recent_documents:
            payload["recent_documents"] = recent_documents
        if sync_error:
            payload["webdav_sync_error"] = sync_error
        return json.dumps(payload, ensure_ascii=False)

    return StructuredTool.from_function(
        search_context,
        name="search_context",
        description=(
            "Search local context and synced readable WebDAV knowledge for relevant information. "
            "Use this for user notes, synced notes, documents, knowledge, and WebDAV context; do not use /memories for user notes. "
            "For recent/latest notes requests, the result may include recent_documents sorted by modified time. "
            "Local hits use /files/... paths; WebDAV hits use /webdav/... paths. Args: query, top_k."
        ),
    )


def _write_context_tool(service: ContextKnowledgeService, webdav_service: WebDAVContextService | None) -> Any:
    from langchain_core.tools import StructuredTool

    def write_context(type: str, absolute_path: str, content: str, mode: str = "append") -> str:
        """Write approved knowledge into the local context.

        Only call this after the user explicitly asks or confirms that this content should be saved.
        Do not call this for personal memory, user preferences, future conversation rules, or "remember this" requests.
        For those, use DeepAgent long-term memory by writing a /memories/... file with the built-in write_file tool.
        absolute_path is a tool path such as /files/wechat.md, not a filesystem path.
        mode must be append, overwrite, or create.
        """
        if str(absolute_path or "").strip().startswith("/webdav/"):
            if webdav_service is None:
                raise RuntimeError("webdav context is not enabled")
            if type.strip() != "knowledge":
                raise RuntimeError("type must be knowledge")
            result = run_async(webdav_service.write(absolute_path=absolute_path, content=content, mode=mode))
        else:
            result = service.write(type=type, absolute_path=absolute_path, content=content, mode=mode)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        write_context,
        name="write_context",
        description=(
            "Write approved knowledge to workspace/context/knowledge/files. "
            "For writable WebDAV permission paths, use /webdav/path.ext; protected paths cannot be written. "
            "Do not use for personal memory, user preferences, future conversation rules, or 'remember this' requests; "
            "use built-in write_file('/memories/...') for those. "
            "Args: type='knowledge', absolute_path like '/files/wechat.md' or '/webdav/00AgentInbox/wechat.md', "
            "content, mode append|overwrite|create. "
            "Only use after explicit user approval."
        ),
    )


def _is_recent_context_query(query: str) -> bool:
    normalized = str(query or "").strip().lower()
    if not normalized:
        return False
    has_recent = any(marker in normalized for marker in ("最近", "近期", "最新", "recent", "latest", "newest"))
    has_context = any(marker in normalized for marker in ("笔记", "文档", "知识", "notes", "note", "docs", "documents"))
    return has_recent and has_context or bool(re.search(r"\b(last|latest|recent)\s+\d*\s*(notes|docs|documents)\b", normalized))
