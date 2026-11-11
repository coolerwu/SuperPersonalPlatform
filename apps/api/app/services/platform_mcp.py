"""Platform business tools exposed to chat through an in-process MCP server.

The runner mounts one ``platform_tools`` SDK MCP server per cached Claude client.
Tool implementations stay in their existing ``*_tools`` modules; this module
only adapts the registered LangChain tools to the SDK's MCP tool shape.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings as app_settings
from app.services import (  # noqa: F401 - imports register the tools
    allocation_tools,
    backtest_tools,
    chan_tools,
    trade_journal_tools,
)
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext

logger = logging.getLogger(__name__)

SERVER_NAME = "platform_tools"
SERVER_VERSION = "1.0.0"
MAX_RESULT_CHARS = 50_000

BUSINESS_TOOL_NAMES = (
    "backtest_run",
    "backtest_status",
    "backtest_result",
    "backtest_cancel",
    "market_probe",
    "market_cache_status",
    "trade_journal_record",
    "trade_journal_update",
    "trade_journal_close",
    "trade_journal_query",
    "trade_journal_stats",
    "allocation_overview",
    "allocation_entries",
    "allocation_portfolio_create",
    "allocation_portfolio_update",
    "allocation_entry_record",
    "allocation_entry_update",
    "chan_analyze",
    "chan_backtest",
)

READ_ONLY_TOOLS = frozenset(
    {
        "backtest_status",
        "backtest_result",
        "market_probe",
        "market_cache_status",
        "trade_journal_query",
        "trade_journal_stats",
        "allocation_overview",
        "allocation_entries",
        "chan_analyze",
        "chan_backtest",
    }
)

DESTRUCTIVE_TOOLS = frozenset(
    {
        "backtest_cancel",
        "trade_journal_update",
        "trade_journal_close",
        "allocation_portfolio_update",
        "allocation_entry_update",
    }
)


def _tool_schema(base: Any) -> dict[str, Any]:
    schema = getattr(base, "args_schema", None)
    empty = {"type": "object", "properties": {}}
    if schema is None:
        return empty
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    if hasattr(schema, "schema"):
        return schema.schema()
    return empty


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n…（结果已截断）"
    return text


def _annotations(name: str) -> Any:
    from claude_agent_sdk import ToolAnnotations

    hints: dict[str, Any] = {"maxResultSizeChars": MAX_RESULT_CHARS}
    if name in READ_ONLY_TOOLS:
        hints["readOnlyHint"] = True
    elif name in DESTRUCTIVE_TOOLS:
        hints["destructiveHint"] = True
    return ToolAnnotations(**hints)


def _adapt_tool(base: Any) -> Any:
    from claude_agent_sdk import tool

    @tool(
        base.name,
        str(base.description or ""),
        _tool_schema(base),
        annotations=_annotations(base.name),
    )
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await base.ainvoke(args)
        except Exception as exc:  # noqa: BLE001 - MCP reports the error text
            logger.exception("platform tool %s failed", base.name)
            message = f"工具执行失败：{type(exc).__name__}: {exc}"
            if len(message) > 4000:
                message = message[:4000] + "…"
            return {
                "content": [{"type": "text", "text": message}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": _result_text(result)}]}

    return handler


def _tool_timeout_ms() -> int:
    wait_seconds = max(0, int(app_settings.backtest_tool_wait_seconds))
    return max(180_000, (wait_seconds + 60) * 1000)


def allowed_tool_names() -> list[str]:
    """Prefixed names the model sees, used for the SDK allow list."""
    return [f"mcp__{SERVER_NAME}__{name}" for name in BUSINESS_TOOL_NAMES]


def build_server(context: SkillToolContext) -> Any:
    """Build the SDK MCP server for one Agent/session context."""
    from claude_agent_sdk import create_sdk_mcp_server

    specs = {spec.name: spec for spec in SKILL_TOOL_REGISTRY.specs()}
    missing = [name for name in BUSINESS_TOOL_NAMES if name not in specs]
    if missing:
        raise RuntimeError(
            "platform tools are not registered: " + ", ".join(missing)
        )
    tools = [_adapt_tool(specs[name].factory(context)) for name in BUSINESS_TOOL_NAMES]
    server = create_sdk_mcp_server(
        name=SERVER_NAME, version=SERVER_VERSION, tools=tools
    )
    # Per-server timeout: the CLI ignores the global idle-timeout env for SDK
    # servers, and backtest_run can stay silent for ~90 seconds. Disable the
    # CLI's auto-background move so the model always gets the inline result.
    server["timeout"] = _tool_timeout_ms()  # type: ignore[typeddict-unknown-key]
    server["disableAutoBackground"] = True  # type: ignore[typeddict-unknown-key]
    return server
