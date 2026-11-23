"""In-process browser MCP server backed by the shared ``BrowserService``.

The runner mounts one ``browser`` SDK MCP server per cached Claude client.
Every tool call borrows the shared browser through ``BrowserService.use()`` and
operates on the tabs owned by the current SDK session, so cookie/login state is
shared while tab names never leak across sessions.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from app.services.browser_tools import MAX_WAIT_FOR_MS, BrowserToolError

if TYPE_CHECKING:
    from app.services.browser_service import BrowserService
    from app.services.browser_tools import BrowserSession

logger = logging.getLogger(__name__)

SERVER_NAME = "browser"
SERVER_VERSION = "1.0.0"
# 5 分钟 wait_for 预算加上启动与快照，必须能放进一次工具调用。
TOOL_TIMEOUT_MS = MAX_WAIT_FOR_MS + 120_000
_DEAD_BROWSER_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed unexpectedly",
    "playwright connection closed",
)
TOOL_NAMES = (
    "navigate",
    "snapshot",
    "click",
    "type",
    "fill_form",
    "press_key",
    "select_option",
    "wait_for",
    "navigate_back",
    "tabs",
    "take_screenshot",
    "console_messages",
    "close",
)

_TAB = {
    "type": "string",
    "description": "Tab name or index; defaults to the session's current tab.",
}
_TARGET = {
    "type": "string",
    "description": "Element ref from snapshot (e.g. s1e3) or a unique CSS selector.",
}


def _text(value: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}]}


def _error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"error: {message}"}],
        "is_error": True,
    }


def _is_dead_browser(message: str) -> bool:
    lowered = message.lower()
    # 网络/DNS 类错误不是浏览器崩溃，重置共享浏览器会误伤所有会话。
    if "net::" in lowered or "err_" in lowered:
        return False
    return any(marker in lowered for marker in _DEAD_BROWSER_MARKERS)


async def _maybe_reset(service: BrowserService, message: str) -> None:
    if not _is_dead_browser(message):
        return
    try:
        await service.reset()
    except Exception:
        logger.warning("browser reset failed after a dead-browser error", exc_info=True)


def _guard_factory(
    service: BrowserService, *, agent_id: str, session_id: str
) -> Callable[[str, Callable[[], Awaitable[dict[str, Any]]]], Awaitable[dict[str, Any]]]:
    async def guard(
        name: str, call: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        try:
            return await call()
        except BrowserToolError as exc:
            message = str(exc)
            logger.debug(
                "browser tool %s rejected (agent=%s session=%s): %s",
                name,
                agent_id,
                session_id,
                message,
            )
            await _maybe_reset(service, message)
            return _error(message)
        except Exception as exc:
            logger.exception(
                "browser tool %s failed (agent=%s session=%s)",
                name,
                agent_id,
                session_id,
            )
            message = f"{type(exc).__name__}: {exc}"[:4000]
            await _maybe_reset(service, message)
            return _error(message)

    return guard


def _workspace_path(workspace: Path, filename: str) -> Path:
    name = str(filename or "").strip()
    if not name:
        raise BrowserToolError("filename must not be empty")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise BrowserToolError("filename escapes the workspace")
    root = Path(workspace).resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise BrowserToolError("filename escapes the workspace") from exc
    return target


def _write_png(path: Path, payload_b64: str) -> None:
    # 解码、建目录与写文件放在同一个线程调用里，避免阻塞事件循环。
    data = base64.b64decode(payload_b64)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _build_tools(
    service: BrowserService, *, agent_id: str, session_id: str, workspace: Path
) -> list[Any]:
    from claude_agent_sdk import tool

    _guard = _guard_factory(service, agent_id=agent_id, session_id=session_id)

    async def tab_for(session: BrowserSession, args: dict[str, Any]) -> str:
        return await service.resolve_tab(session, session_id, args.get("tab"))

    @tool(
        "navigate",
        "Open an HTTP/HTTPS URL in a tab and return the page snapshot "
        "(title, text, and interactive element refs).",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open."},
                "tab": _TAB,
            },
            "required": ["url"],
        },
    )
    async def navigate(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            url = str(args.get("url") or "").strip()
            if not url:
                raise BrowserToolError("url must not be empty")
            async with service.use() as session:
                tab = await service.prepare_tab(
                    session, session_id, args.get("tab")
                )
                return _text(await session.open(url, tab))

        return await _guard("navigate", call)

    @tool(
        "snapshot",
        "Read the current page snapshot with fresh element refs. "
        "Old refs expire after every snapshot.",
        {"type": "object", "properties": {"tab": _TAB}},
    )
    async def snapshot(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(await session.snapshot(tab))

        return await _guard("snapshot", call)

    @tool(
        "click",
        "Click an element by snapshot ref or unique CSS selector and return "
        "the new page snapshot.",
        {
            "type": "object",
            "properties": {"target": _TARGET, "tab": _TAB},
            "required": ["target"],
        },
    )
    async def click(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(
                    await session.click(str(args.get("target") or ""), tab)
                )

        return await _guard("click", call)

    @tool(
        "type",
        "Type text into an input by snapshot ref or CSS selector; "
        "submit=true presses Enter afterwards.",
        {
            "type": "object",
            "properties": {
                "target": _TARGET,
                "text": {"type": "string", "description": "Text to type."},
                "submit": {
                    "type": "boolean",
                    "description": "Press Enter after typing.",
                },
                "tab": _TAB,
            },
            "required": ["target", "text"],
        },
    )
    async def type_text(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(
                    await session.fill(
                        str(args.get("target") or ""),
                        str(args.get("text") or ""),
                        tab,
                        submit=bool(args.get("submit")),
                    )
                )

        return await _guard("type", call)

    @tool(
        "fill_form",
        "Fill several inputs at once, each field given as target and value.",
        {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "description": "Fields to fill.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": _TARGET,
                            "value": {
                                "type": "string",
                                "description": "Value to type.",
                            },
                        },
                        "required": ["target", "value"],
                    },
                },
                "tab": _TAB,
            },
            "required": ["fields"],
        },
    )
    async def fill_form(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            fields = args.get("fields")
            if not isinstance(fields, list):
                raise BrowserToolError("fields must be a list")
            if len(fields) > 20:
                # 每个字段最多等 10 秒，超过 20 个会撑爆工具调用预算。
                raise BrowserToolError("fill_form accepts at most 20 fields")
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(await session.fill_many(fields, tab))

        return await _guard("fill_form", call)

    @tool(
        "press_key",
        "Press a keyboard key on the page (e.g. Enter, Escape, Tab, Control+A) "
        "and return the new snapshot.",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to press."},
                "tab": _TAB,
            },
            "required": ["key"],
        },
    )
    async def press_key(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(await session.press(str(args.get("key") or ""), tab))

        return await _guard("press_key", call)

    @tool(
        "select_option",
        "Select option value(s) in a dropdown by snapshot ref or CSS selector.",
        {
            "type": "object",
            "properties": {
                "target": _TARGET,
                "values": {
                    "type": "array",
                    "description": "Option values or labels to select.",
                    "items": {"type": "string"},
                },
                "tab": _TAB,
            },
            "required": ["target", "values"],
        },
    )
    async def select_option(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            values = [str(value) for value in args.get("values") or []]
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(
                    await session.select_option(
                        str(args.get("target") or ""), values, tab
                    )
                )

        return await _guard("select_option", call)

    @tool(
        "wait_for",
        "Wait until text appears (text), disappears (textGone), or a number of "
        "milliseconds elapse (time).",
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Wait until this text is visible.",
                },
                "textGone": {
                    "type": "string",
                    "description": "Wait until this text disappears.",
                },
                "time": {
                    "type": "integer",
                    "description": "Wait this many milliseconds.",
                },
                "tab": _TAB,
            },
        },
    )
    async def wait_for(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            time_value = args.get("time")
            time_ms = int(time_value) if time_value is not None else None
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(
                    await session.wait_for(
                        tab,
                        text=args.get("text"),
                        text_gone=args.get("textGone"),
                        time_ms=time_ms,
                    )
                )

        return await _guard("wait_for", call)

    @tool(
        "navigate_back",
        "Go back to the previous page in the tab and return the new snapshot.",
        {"type": "object", "properties": {"tab": _TAB}},
    )
    async def navigate_back(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                return _text(await session.navigate_back(tab))

        return await _guard("navigate_back", call)

    @tool(
        "tabs",
        "Manage this session's tabs: list, open a new tab, select one (needs "
        "index), or close one (needs index). The separate close tool closes all "
        "tabs when tab is omitted.",
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "new", "select", "close"],
                    "description": "Tab action to perform.",
                },
                "index": {
                    "type": "integer",
                    "description": "Tab index for select or close.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to open in a new tab.",
                },
            },
            "required": ["action"],
        },
    )
    async def tabs(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            action = str(args.get("action") or "").strip()
            # 未知名或缺失下标时先失败，不能因为一次坏调用懒启动 Chromium。
            if action not in ("list", "new", "select", "close"):
                raise BrowserToolError("action must be list, new, select, or close")
            index = args.get("index")
            if action in ("select", "close"):
                if index is None:
                    raise BrowserToolError(
                        f"tabs {action} needs index; "
                        "use the close tool to close all tabs"
                    )
                try:
                    index = int(index)
                except (TypeError, ValueError) as exc:
                    raise BrowserToolError(
                        f"tabs {action} needs index; "
                        "use the close tool to close all tabs"
                    ) from exc
            async with service.use() as session:
                if action == "list":
                    rows = await service.list_tabs(session, session_id)
                    if not rows:
                        return _text(
                            "No tabs are open for this session; call navigate first."
                        )
                    lines = [
                        f"{row['index']}. {row['name']} {row['url']}"
                        + ("  <- current" if row["default"] else "")
                        for row in rows
                    ]
                    return _text("\n".join(lines))
                if action == "new":
                    info = await service.new_tab(
                        session, session_id, url=args.get("url")
                    )
                    return _text(f"Opened tab {info['index']} ({info['name']}).")
                if action == "select":
                    name = await service.select_tab(session, session_id, index)
                    return _text(f"Selected tab {name!r}.")
                return _text(
                    await service.close_tabs(session, session_id, index)
                )

        return await _guard("tabs", call)

    @tool(
        "take_screenshot",
        "Capture the tab as a PNG image; optionally save it inside the workspace "
        "and return the relative path.",
        {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Workspace-relative .png path to save the image.",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the whole scrollable page.",
                },
                "tab": _TAB,
            },
        },
    )
    async def take_screenshot(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            target_path: Path | None = None
            relative = ""
            if args.get("filename"):
                raw = str(args["filename"]).strip()
                # 后缀与越界校验放在截图之前，坏文件名不应启动浏览器。
                if not raw.lower().endswith(".png"):
                    raise BrowserToolError("filename must end with the .png suffix")
                target_path = _workspace_path(workspace, raw)
                relative = target_path.relative_to(Path(workspace).resolve()).as_posix()
            async with service.use() as session:
                tab = await tab_for(session, args)
                blocks = await session.screenshot(
                    tab, full_page=bool(args.get("full_page"))
                )
            header = str(blocks[0].get("text") or "")
            payload = str(blocks[1]["image_url"]["url"]).split(",", 1)[1]
            if target_path is not None:
                await asyncio.to_thread(_write_png, target_path, payload)
                header = f"{header}\nSaved to {relative}"
            return {
                "content": [
                    {"type": "text", "text": header},
                    {"type": "image", "data": payload, "mimeType": "image/png"},
                ]
            }

        return await _guard("take_screenshot", call)

    @tool(
        "console_messages",
        "Return the browser console messages recorded for the tab.",
        {"type": "object", "properties": {"tab": _TAB}},
    )
    async def console_messages(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                tab = await tab_for(session, args)
                entries = await session.console_messages(tab)
            if not entries:
                return _text("No console messages in this tab.")
            lines = [
                f"{entry.get('time', '')} [{entry['type']}] {entry['text']}"
                for entry in entries
            ]
            return _text("\n".join(lines))

        return await _guard("console_messages", call)

    @tool(
        "close",
        "Close one of this session's tabs, or every tab of this session when tab "
        "is omitted.",
        {
            "type": "object",
            "properties": {
                "tab": {
                    "type": "string",
                    "description": "标签页名称或序号；省略时关闭本会话全部标签页",
                }
            },
        },
    )
    async def close(args: dict[str, Any]) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            async with service.use() as session:
                return _text(
                    await service.close_tabs(
                        session, session_id, args.get("tab")
                    )
                )

        return await _guard("close", call)

    return [
        navigate,
        snapshot,
        click,
        type_text,
        fill_form,
        press_key,
        select_option,
        wait_for,
        navigate_back,
        tabs,
        take_screenshot,
        console_messages,
        close,
    ]


def build_server(
    service: BrowserService, *, agent_id: str, session_id: str, workspace: Path
) -> Any:
    """Build the in-process ``browser`` MCP server for one Agent/session."""
    from claude_agent_sdk import create_sdk_mcp_server

    server = create_sdk_mcp_server(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        tools=_build_tools(
            service,
            agent_id=agent_id,
            session_id=session_id,
            workspace=workspace,
        ),
    )
    # 与 platform_tools 一致：单次工具调用留给 5 分钟 wait_for，且不自动转后台。
    server["timeout"] = TOOL_TIMEOUT_MS  # type: ignore[typeddict-unknown-key]
    server["disableAutoBackground"] = True  # type: ignore[typeddict-unknown-key]
    return server


def allowed_tool_names() -> list[str]:
    """Prefixed names the model sees, used for the SDK allow list."""
    return [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]
