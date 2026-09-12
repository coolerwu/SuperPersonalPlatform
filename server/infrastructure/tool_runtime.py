from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app.code_execution_service import CodeExecutionService
from server.app.session_service import SessionService
from server.app.webdav_context_service import WebDAVContextService
from server.domain.tooling import get_tool_definition
from server.infrastructure.browser_tools import build_browser_extract_tool, build_browser_search_tool
from server.infrastructure.config import load_settings
from server.domain.agent_config import AgentWebDAVConfig
from server.infrastructure.agent_workspace import WebDAVPathPolicy
from server.infrastructure.webdav_backend import AgentWebDAVView


_ARXIV_RATE_LIMIT_SECONDS = 3.0
_ARXIV_RATE_LIMIT_LOCK = threading.Lock()
_LAST_ARXIV_REQUEST_AT = 0.0


@dataclass(frozen=True)
class PlatformToolContext:
    run_id: str
    source: str
    agent_id: str
    session_id: str
    metadata: dict[str, Any]


def _tool_error_result(
    tool: str,
    exc: Exception,
    *,
    message: str | None = None,
    recoverable: bool = True,
    suggestions: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "ok": False,
            "tool": tool,
            "recoverable": recoverable,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
            "message": message or f"{tool} could not complete. Treat this as a tool observation and choose a fallback.",
            "suggestions": suggestions
            or [
                "Try another available tool or source.",
                "Explain the limitation to the user if no fallback is available.",
            ],
        },
        ensure_ascii=False,
    )


def build_platform_tools(
    tool_ids: tuple[str, ...],
    *,
    context_workspace: Path,
    schedule_service: Any = None,
    tool_context: PlatformToolContext | None = None,
    file_backend: Any = None,
) -> list[Any]:
    tools = []
    browser_config = _browser_config(context_workspace)
    for tool_id in tool_ids:
        definition = get_tool_definition(tool_id)
        if definition.id == "send_attachment":
            if tool_context is not None and file_backend is not None:
                tools.append(_send_attachment_tool(context_workspace.parent, tool_context, file_backend))
        elif definition.id == "search_session":
            if tool_context is None:
                continue
            tools.append(_search_session_tool(SessionService(context_workspace.parent), tool_context))
        elif definition.id == "arxiv":
            tools.append(_arxiv_tool())
        elif definition.id == "yahoo_finance_news":
            tools.append(_yahoo_finance_news_tool())
        elif definition.id == "browser_extract":
            tool_workspace = context_workspace.parent if tool_context is not None else None
            tool_agent_id = tool_context.agent_id if tool_context is not None else ""
            browser_kwargs = {
                "proxy": browser_config.get("proxy", ""),
                "timeout_ms": int(browser_config.get("timeout_ms") or 60000),
                "allow_private_hosts": tuple(browser_config.get("allow_private_hosts") or ()),
                "workspace": tool_workspace,
                "agent_id": tool_agent_id,
            }
            tools.append(
                build_browser_extract_tool(**browser_kwargs)
            )
            tools.append(build_browser_search_tool(**browser_kwargs))
        elif definition.id == "schedule":
            if schedule_service is None or tool_context is None:
                continue
            tools.append(_schedule_tool(schedule_service, tool_context))
        elif definition.id == "execute_code":
            if tool_context is None:
                continue
            tools.append(_execute_code_tool(context_workspace.parent, tool_context))
    return tools


def _webdav_context_service(context_workspace: Path, webdav: AgentWebDAVConfig = AgentWebDAVConfig()) -> AgentWebDAVView | None:
    workspace = context_workspace.parent
    try:
        settings = load_settings(workspace / "config.yaml")
    except Exception:
        return None
    if not settings.nutstore.enabled or not settings.context.webdav_sync.enabled:
        return None
    if not webdav.enabled:
        return None
    return AgentWebDAVView(WebDAVContextService(
        workspace=workspace, nutstore=settings.nutstore, context=settings.context,
    ), WebDAVPathPolicy(webdav))


def _browser_config(context_workspace: Path) -> dict[str, Any]:
    workspace = context_workspace.parent
    try:
        settings = load_settings(workspace / "config.yaml")
    except Exception:
        return {}
    return {
        "proxy": settings.browser.proxy,
        "timeout_ms": settings.browser.timeout_ms,
        "allow_private_hosts": settings.browser.allow_private_hosts,
    }


def _send_attachment_tool(workspace: Path, context: PlatformToolContext, backend: Any) -> Any:
    from langchain_core.tools import StructuredTool
    from server.infrastructure.outgoing_attachments import queue_attachment
    from server.infrastructure.agent_workspace import workspace_member_path

    def send_attachment(file_path: str, kind: str = "auto") -> str:
        try:
            from server.app.run_delivery_service import _delivery_target
            if not context.run_id or any(c in context.run_id for c in ("/", "\\")) or context.run_id in {".", ".."}:
                raise ValueError("Missing current run")
            run_dir = workspace_member_path(workspace, "runs", context.run_id)
            run_input = json.loads((run_dir / "input.json").read_text())
            if run_input.get("agent_id") != context.agent_id or not _delivery_target({"input": run_input}):
                raise ValueError("This run has no current WeChat delivery target")
            item = queue_attachment(run_dir, backend, file_path, kind)
            return json.dumps({"ok": True, "status": "queued", "attachment": item,
                "message": "Queued for delivery after this run completes; not yet sent."}, ensure_ascii=False)
        except (ValueError, OSError, RuntimeError) as exc:
            return _tool_error_result("send_attachment", exc)
    return StructuredTool.from_function(send_attachment, name="send_attachment", description=(
        "Send an existing file or image to the current WeChat conversation when the user asks for it. "
        "Use an absolute file-tool path such as /artifacts/report.pdf, /files/document.docx or an authorized /webdav/... path. "
        "kind=auto detects PNG/JPEG/GIF/WebP as images; kind=file sends the original as an attachment. "
        "Max 20 MiB per file, 10 per run. Queues a frozen copy for delivery after successful run completion. "
        "Do not claim delivery already succeeded; do not pass URLs, host paths or recipients. "
        "This tool does not generate images and does not write to WebDAV."
    ))


def _execute_code_tool(workspace: Path, tool_context: PlatformToolContext) -> Any:
    from langchain_core.tools import StructuredTool

    def execute_code(language: str, code: str, files: list[dict[str, Any]] | None = None) -> str:
        """Run short Python or shell code inside the configured Docker + gVisor sandbox.

        Use this for calculation, text/data transformation, small script experiments, and artifact generation.
        The sandbox has no network and can only access the input/work/output mounts prepared for this execution.
        Write files that should be returned to /workspace/output.
        """
        try:
            settings = load_settings(workspace / "config.yaml")
            service = CodeExecutionService(workspace, settings.code_execution)
            return service.execute_sync(
                language=language,
                code=code,
                files=tuple(files or ()),
                agent_id=tool_context.agent_id,
                run_id=tool_context.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return _tool_error_result(
                "execute_code",
                exc,
                suggestions=[
                    "If code execution is disabled or unavailable, solve the task without running code when practical.",
                    "If Docker/gVisor is missing, tell the user the configured sandbox runtime is unavailable.",
                ],
            )

    return StructuredTool.from_function(
        execute_code,
        name="execute_code",
        description=(
            "Execute short Python or POSIX shell code inside Docker using the configured gVisor runtime. "
            "The tool never falls back to host subprocess. It requires code_execution.enabled=true, Docker, runsc, and the configured image. "
            "Network is disabled, no host secrets are passed, and only prepared /workspace/input, /workspace/work, and /workspace/output mounts are visible. "
            "Use language='python' for Python 3.12 scripts or language='shell' for /bin/sh scripts. "
            "Write generated files to /workspace/output to receive /artifacts/... paths. The script is saved under /scratch/ and returned as script_path; temporary input/work/output mounts are removed after execution. Args: language, code, optional files=[{path, content}]."
        ),
    )


def _search_session_tool(session_service: SessionService, tool_context: PlatformToolContext) -> Any:
    from langchain_core.tools import StructuredTool

    def search_session(query: str, top_k: int = 8, role: str = "", scope: str = "current") -> str:
        """Search saved session messages by keyword.

        By default this searches the active run's current session. Set scope="related" to search
        sessions from the same channel/account/peer/agent, including archived sessions after context clears.
        Use it when the user refers to earlier messages, constraints, images, links, or decisions from this same chat.
        """
        session_id = str(tool_context.session_id or "").strip()
        if not session_id:
            return json.dumps(
                {
                    "hits": [],
                    "message": "This run has no session_id, so there is no conversation history to search.",
                },
                ensure_ascii=False,
            )
        if not session_service.exists(session_id):
            return json.dumps({"hits": [], "message": "Current session does not exist."}, ensure_ascii=False)
        normalized_scope = str(scope or "current").strip().lower()
        if normalized_scope in {"related", "all_related", "sessions"}:
            sessions = session_service.search_related_sessions(
                session_id,
                query=str(query or ""),
                limit=min(max(int(top_k or 8), 1), 20),
                role=str(role or ""),
            )
            return json.dumps(
                {
                    "scope": "related",
                    "session_id": session_id,
                    "sessions": [
                        {
                            "session": group["session"],
                            "score": group["score"],
                            "hits": [_public_session_message(message) for message in group["hits"]],
                        }
                        for group in sessions
                    ],
                },
                ensure_ascii=False,
            )
        hits = session_service.search_messages(
            session_id,
            query=str(query or ""),
            limit=min(max(int(top_k or 8), 1), 20),
            role=str(role or ""),
        )
        return json.dumps(
            {
                "scope": "current",
                "session_id": session_id,
                "hits": [_public_session_message(message) for message in hits],
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        search_session,
        name="search_session",
        description=(
            "Search conversation session history by keyword. "
            "By default this searches the current session; set scope='related' to search sessions from the same channel/account/peer/agent, including archived sessions after a context clear. "
            "The session identity is automatically scoped to the current run; do not provide a session_id. "
            "Use this when the user says things like '刚才', '前面', '之前', '那张图', '那个链接', or refers to prior constraints. "
            "Args: query, top_k, optional role ('user' or 'assistant'), optional scope ('current' or 'related')."
        ),
    )


def _public_session_message(message: dict[str, Any]) -> dict[str, Any]:
    attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
    return {
        "seq": message.get("seq"),
        "role": message.get("role", ""),
        "created_at": message.get("created_at", ""),
        "run_id": message.get("run_id", ""),
        "content": _message_snippet(str(message.get("content") or ""), max_chars=900),
        "attachments": [
            {
                "id": item.get("id", ""),
                "type": item.get("type", ""),
                "mime": item.get("mime", ""),
                "filename": item.get("filename", ""),
                "workspace_path": item.get("workspace_path", ""),
            }
            for item in attachments
            if isinstance(item, dict)
        ],
    }


def _message_snippet(content: str, *, max_chars: int) -> str:
    text = " ".join(str(content or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _arxiv_tool() -> Any:
    from langchain_core.tools import StructuredTool

    def arxiv(query: str, top_k: int = 3) -> str:
        """Search arXiv papers. Consecutive requests are rate-limited to at least 3 seconds."""
        _ensure_user_agent()
        _wait_for_arxiv_rate_limit()
        try:
            from langchain_community.utilities.arxiv import ArxivAPIWrapper
            wrapper = ArxivAPIWrapper(
                top_k_results=min(max(int(top_k or 3), 1), 10),
                doc_content_chars_max=4000,
            )
            return wrapper.run(str(query or "").strip())
        except Exception as exc:  # noqa: BLE001
            return _tool_error_result(
                "arxiv",
                exc,
                suggestions=[
                    "Use browser_search for public web discovery if available.",
                    "Answer from existing context and state that arXiv lookup failed if no fallback is available.",
                ],
            )

    return StructuredTool.from_function(
        arxiv,
        name="arxiv",
        description=(
            "Search arXiv for academic papers and return titles, authors, summaries, and links. "
            "Free public source; requests are throttled to one call every 3 seconds. Args: query, top_k."
        ),
    )


def _wait_for_arxiv_rate_limit() -> None:
    global _LAST_ARXIV_REQUEST_AT
    with _ARXIV_RATE_LIMIT_LOCK:
        now = time.monotonic()
        wait_seconds = _ARXIV_RATE_LIMIT_SECONDS - (now - _LAST_ARXIV_REQUEST_AT)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _LAST_ARXIV_REQUEST_AT = time.monotonic()


def _yahoo_finance_news_tool() -> Any:
    from langchain_core.tools import StructuredTool

    def yahoo_finance_news(ticker: str, top_k: int = 5) -> str:
        """Fetch lightweight finance news for a public ticker via Yahoo Finance."""
        _ensure_user_agent()
        try:
            from langchain_community.tools.yahoo_finance_news import YahooFinanceNewsTool
            tool = YahooFinanceNewsTool(top_k=min(max(int(top_k or 5), 1), 10))
            return str(tool.invoke({"query": str(ticker or "").strip().upper()}))
        except Exception as exc:  # noqa: BLE001
            return _tool_error_result(
                "yahoo_finance_news",
                exc,
                suggestions=[
                    "Use browser_search for public company news if available.",
                    "Answer from existing context and state that Yahoo Finance lookup failed if no fallback is available.",
                ],
            )

    return StructuredTool.from_function(
        yahoo_finance_news,
        name="yahoo_finance_news",
        description=(
            "Fetch lightweight financial news for a public company ticker, for example AAPL or MSFT. "
            "Use for quick finance-news context, not trading-grade market data. Args: ticker, top_k."
        ),
    )


def _ensure_user_agent() -> None:
    os.environ.setdefault("USER_AGENT", "SuperPersonalPlatform/0.1")


def _schedule_tool(schedule_service: Any, tool_context: PlatformToolContext) -> Any:
    from langchain_core.tools import StructuredTool

    def schedule(
        action: str,
        schedule_id: str = "",
        name: str = "",
        prompt: str = "",
        trigger_kind: str = "",
        interval_minutes: int = 0,
        run_at: str = "",
        cron: str = "",
        timezone: str = "Asia/Shanghai",
        enabled: bool | None = None,
    ) -> str:
        """Manage scheduled tasks owned by this Agent and this conversation.

        Use create for user-approved reminders or recurring Agent tasks.
        Use list/get/update/delete only for schedules previously created by this tool in the current conversation.
        Scheduled Agent results are delivered back to the current channel when channel delivery context is available.
        """
        try:
            normalized_action = str(action or "").strip().lower()
            if normalized_action == "create":
                if tool_context.source == "chat_group":
                    return json.dumps({"ok": False, "error": "群聊暂不支持定时协作。请在单聊或定时任务页面创建任务。"}, ensure_ascii=False)
                return json.dumps(
                    _schedule_create(
                        schedule_service,
                        tool_context,
                        schedule_id=schedule_id,
                        name=name,
                        prompt=prompt,
                        trigger_kind=trigger_kind,
                        interval_minutes=interval_minutes,
                        run_at=run_at,
                        cron=cron,
                        timezone=timezone,
                        enabled=enabled,
                    ),
                    ensure_ascii=False,
                )
            if normalized_action == "list":
                return json.dumps(_schedule_list(schedule_service, tool_context), ensure_ascii=False)
            if normalized_action == "get":
                return json.dumps(_schedule_get(schedule_service, tool_context, schedule_id), ensure_ascii=False)
            if normalized_action == "update":
                return json.dumps(
                    _schedule_update(
                        schedule_service,
                        tool_context,
                        schedule_id=schedule_id,
                        name=name,
                        prompt=prompt,
                        trigger_kind=trigger_kind,
                        interval_minutes=interval_minutes,
                        run_at=run_at,
                        cron=cron,
                        timezone=timezone,
                        enabled=enabled,
                    ),
                    ensure_ascii=False,
                )
            if normalized_action == "delete":
                return json.dumps(_schedule_delete(schedule_service, tool_context, schedule_id), ensure_ascii=False)
            raise RuntimeError("action must be create, list, get, update, or delete")
        except Exception as exc:  # noqa: BLE001
            return _tool_error_result(
                "schedule",
                exc,
                suggestions=[
                    "Ask the user for missing schedule fields if validation failed.",
                    "Do not try to manage schedules outside the current agent/session ownership boundary.",
                ],
            )

    return StructuredTool.from_function(
        schedule,
        name="schedule",
        description=(
            "Manage scheduled Agent tasks for the current conversation. "
            "Actions: create, list, get, update, delete. "
            "Only schedules created by this tool for the current agent and session can be read, updated, or deleted. "
            "For create/update pass trigger_kind='once' with run_at ISO datetime, trigger_kind='interval' with interval_minutes>=1, "
            "or trigger_kind='cron' with a 5-field cron expression and timezone. "
            "Results are delivered back to the current WeChat conversation when this run came from WeChat. "
            "Args: action, schedule_id, name, prompt, trigger_kind, interval_minutes, run_at, cron, timezone, enabled."
        ),
    )


def _schedule_create(
    schedule_service: Any,
    tool_context: PlatformToolContext,
    *,
    schedule_id: str,
    name: str,
    prompt: str,
    trigger_kind: str,
    interval_minutes: int,
    run_at: str,
    cron: str,
    timezone: str,
    enabled: bool | None,
) -> dict[str, Any]:
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise RuntimeError("prompt is required")
    next_schedule_id = _safe_schedule_id(schedule_id) or _new_tool_schedule_id()
    payload = {
        "id": next_schedule_id,
        "name": str(name or "").strip() or next_schedule_id,
        "enabled": True if enabled is None else bool(enabled),
        "trigger": _tool_trigger_payload(
            trigger_kind=trigger_kind,
            interval_minutes=interval_minutes,
            run_at=run_at,
            cron=cron,
            timezone=timezone,
        ),
        "agent_id": tool_context.agent_id,
        "prompt": prompt_text,
        "session_id": tool_context.session_id,
        "metadata": _tool_schedule_metadata(tool_context),
    }
    created = schedule_service.create_schedule(payload)
    return _tool_schedule_response(created)


def _schedule_list(schedule_service: Any, tool_context: PlatformToolContext) -> dict[str, Any]:
    owned: list[dict[str, Any]] = []
    for summary in schedule_service.list_schedules():
        raw_summary = summary.get("summary") if isinstance(summary.get("summary"), dict) else {}
        raw_definition = summary.get("definition") if isinstance(summary.get("definition"), dict) else {}
        schedule_id = str(summary.get("id") or raw_summary.get("id") or raw_definition.get("id") or "")
        if not schedule_id:
            continue
        try:
            detail = summary if isinstance(summary.get("definition"), dict) else schedule_service.get_schedule(schedule_id)
        except Exception:
            continue
        if _tool_can_manage(detail, tool_context):
            owned.append(_tool_schedule_response(detail))
    return {"schedules": owned}


def _schedule_get(schedule_service: Any, tool_context: PlatformToolContext, schedule_id: str) -> dict[str, Any]:
    detail = schedule_service.get_schedule(_required_schedule_id(schedule_id))
    if not _tool_can_manage(detail, tool_context):
        raise RuntimeError("schedule is not owned by this agent/session")
    return _tool_schedule_response(detail)


def _schedule_update(
    schedule_service: Any,
    tool_context: PlatformToolContext,
    *,
    schedule_id: str,
    name: str,
    prompt: str,
    trigger_kind: str,
    interval_minutes: int,
    run_at: str,
    cron: str,
    timezone: str,
    enabled: bool | None,
) -> dict[str, Any]:
    current = schedule_service.get_schedule(_required_schedule_id(schedule_id))
    if not _tool_can_manage(current, tool_context):
        raise RuntimeError("schedule is not owned by this agent/session")
    definition = current.get("definition") if isinstance(current.get("definition"), dict) else {}
    payload = {
        "id": definition.get("id", schedule_id),
        "name": str(name or "").strip() or definition.get("name") or schedule_id,
        "enabled": bool(definition.get("enabled", True)) if enabled is None else bool(enabled),
        "trigger": (
            _tool_trigger_payload(
                trigger_kind=trigger_kind,
                interval_minutes=interval_minutes,
                run_at=run_at,
                cron=cron,
                timezone=timezone,
            )
            if str(trigger_kind or "").strip()
            else definition.get("trigger")
        ),
        "agent_id": tool_context.agent_id,
        "prompt": str(prompt or "").strip() or definition.get("prompt") or "",
        "session_id": tool_context.session_id,
        "metadata": definition.get("metadata") if isinstance(definition.get("metadata"), dict) else {},
    }
    updated = schedule_service.update_schedule(str(definition.get("id") or schedule_id), payload)
    return _tool_schedule_response(updated)


def _schedule_delete(schedule_service: Any, tool_context: PlatformToolContext, schedule_id: str) -> dict[str, Any]:
    target_id = _required_schedule_id(schedule_id)
    detail = schedule_service.get_schedule(target_id)
    if not _tool_can_manage(detail, tool_context):
        raise RuntimeError("schedule is not owned by this agent/session")
    schedule_service.delete_schedule(target_id)
    return {"schedule_id": target_id, "status": "deleted"}


def _tool_schedule_metadata(tool_context: PlatformToolContext) -> dict[str, Any]:
    metadata = {
        "created_by": {
            "type": "agent_tool",
            "agent_id": tool_context.agent_id,
            "run_id": tool_context.run_id,
            "session_id": tool_context.session_id,
            "source": tool_context.source,
        }
    }
    delivery = _tool_delivery_metadata(tool_context)
    if delivery:
        metadata["delivery"] = delivery
    return metadata


def _tool_delivery_metadata(tool_context: PlatformToolContext) -> dict[str, Any]:
    if tool_context.source != "wechat":
        return {}
    metadata = tool_context.metadata if isinstance(tool_context.metadata, dict) else {}
    account_id = str(metadata.get("account_id") or "").strip()
    to_user_id = str(metadata.get("from_user_id") or metadata.get("peer_id") or "").strip()
    context_token = str(metadata.get("context_token") or "").strip()
    if not account_id or not to_user_id:
        return {}
    return {
        "channel": "wechat",
        "account_id": account_id,
        "session_id": tool_context.session_id,
        "peer_id": str(metadata.get("peer_id") or to_user_id).strip(),
        "peer_type": str(metadata.get("peer_type") or "private").strip(),
        "to_user_id": to_user_id,
        "context_token": context_token,
    }


def _tool_can_manage(detail: dict[str, Any], tool_context: PlatformToolContext) -> bool:
    definition = detail.get("definition") if isinstance(detail.get("definition"), dict) else {}
    metadata = definition.get("metadata") if isinstance(definition.get("metadata"), dict) else {}
    created_by = metadata.get("created_by") if isinstance(metadata.get("created_by"), dict) else {}
    return (
        created_by.get("type") == "agent_tool"
        and created_by.get("agent_id") == tool_context.agent_id
        and created_by.get("session_id") == tool_context.session_id
    )


def _tool_trigger_payload(
    *,
    trigger_kind: str,
    interval_minutes: int,
    run_at: str,
    cron: str,
    timezone: str,
) -> dict[str, Any]:
    kind = str(trigger_kind or "").strip().lower()
    if kind == "once":
        expr = str(run_at or "").strip()
        if not expr:
            raise RuntimeError("run_at is required for once schedules")
        return {"kind": "once", "expr": expr}
    if kind == "interval":
        minutes = int(interval_minutes or 0)
        if minutes < 1:
            raise RuntimeError("interval_minutes must be at least 1")
        return {"kind": "interval", "seconds": minutes * 60}
    if kind == "cron":
        expr = str(cron or "").strip()
        if not expr:
            raise RuntimeError("cron is required for cron schedules")
        return {"kind": "cron", "expr": expr, "timezone": str(timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"}
    raise RuntimeError("trigger_kind must be once, interval, or cron")


def _tool_schedule_response(detail: dict[str, Any]) -> dict[str, Any]:
    definition = detail.get("definition") if isinstance(detail.get("definition"), dict) else {}
    state = detail.get("state") if isinstance(detail.get("state"), dict) else {}
    metadata = definition.get("metadata") if isinstance(definition.get("metadata"), dict) else {}
    return {
        "schedule_id": definition.get("id", ""),
        "name": definition.get("name", ""),
        "agent_id": definition.get("agent_id", ""),
        "prompt": definition.get("prompt", ""),
        "enabled": definition.get("enabled", False),
        "trigger": definition.get("trigger", {}),
        "session_id": definition.get("session_id", ""),
        "delivery": metadata.get("delivery") if isinstance(metadata.get("delivery"), dict) else {},
        "state": {
            "status": state.get("status", ""),
            "next_run_at": state.get("next_run_at", ""),
            "last_run_at": state.get("last_run_at", ""),
            "last_run_id": state.get("last_run_id", ""),
            "last_error": state.get("last_error"),
        },
    }


def _required_schedule_id(schedule_id: str) -> str:
    normalized = _safe_schedule_id(schedule_id)
    if not normalized:
        raise RuntimeError("schedule_id is required")
    return normalized


def _safe_schedule_id(schedule_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(schedule_id or "").strip()).strip("._-")
    return normalized[:80]


def _new_tool_schedule_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"agent_schedule_{timestamp}_{uuid.uuid4().hex[:8]}"
