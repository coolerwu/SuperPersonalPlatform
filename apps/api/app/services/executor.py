from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.exceptions import ModelError
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    ToolMessageChunk,
)

from app.config import settings
from app.services.browser_tools import BROWSER_TOOL_NAMES, BrowserSession
from app.services.skill_tools import SKILL_TOOL_REGISTRY, SkillToolContext, SkillToolRegistry
from app.services.workspace import WorkspaceError, resolve_workspace_path

logger = logging.getLogger(__name__)
_BROWSER_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()

STEP_LIMIT_NOTICE = "（已达到执行步骤上限，以下是当前进展总结）"
STEP_LIMIT_CONTINUE_PROMPT = (
    "上一轮因执行步骤达到上限被中断。继续完成原任务，不要重复已经完成的步骤，也不要重新开始。"
)
STEP_LIMIT_FINAL_PROMPT = (
    "本轮执行步骤已达到上限。请不要再调用工具，基于以上进展直接给出最终答复："
    "已经完成了什么、得到了哪些关键结果、还有哪些没做；不要编造未执行的操作或未得到的结果。"
)
MAX_STEP_LIMIT_CONTINUATIONS = 1


def _log_retryable(
    exc: BaseException, *, agent_name: str, session_id: str
) -> bool:
    if not isinstance(exc, Exception):
        return False
    retryable = exc.is_retryable if isinstance(exc, ModelError) else True
    if retryable:
        logger.warning(
            "model call failed (retryable) agent=%s session=%s error=%s: %s",
            agent_name,
            session_id,
            type(exc).__name__,
            exc,
        )
    return retryable


@dataclass(frozen=True)
class ExecutorEvent:
    type: str
    payload: dict[str, Any]


class UsageCollector(AsyncCallbackHandler):
    run_inline = True
    raise_error = False

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[dict[str, Any]] = []

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            generation = response.generations[0][0]
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
        except Exception:
            usage = None
        if not usage:
            return
        input_tokens = _int_or_none(usage.get("input_tokens"))
        output_tokens = _int_or_none(usage.get("output_tokens"))
        total_tokens = _int_or_none(usage.get("total_tokens"))
        model_name = ""
        try:
            model_name = str((getattr(response, "llm_output", None) or {}).get("model_name") or "")
        except Exception:
            model_name = ""
        self.entries.append(
            {
                "call_id": str(kwargs.get("run_id") or uuid.uuid4().hex),
                "model": model_name,
                "input_tokens": input_tokens or 0,
                "output_tokens": output_tokens or 0,
                "total_tokens": total_tokens or ((input_tokens or 0) + (output_tokens or 0)),
            }
        )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _message_text(message: AIMessageChunk) -> str:
    return _content_text(message.content)


def _drop_dangling_tool_calls(messages: Sequence[Any]) -> list[Any]:
    """Drop trailing assistant tool calls whose tool results were never written."""
    trimmed = list(messages)
    while trimmed:
        last = trimmed[-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            trimmed.pop()
            continue
        break
    return trimmed


def _split_stream_chunk(chunk: Any) -> tuple[tuple[str, ...], Any]:
    if isinstance(chunk, tuple):
        if len(chunk) == 2 and isinstance(chunk[0], tuple):
            return tuple(str(item) for item in chunk[0]), chunk[1]
        if len(chunk) == 3 and isinstance(chunk[0], tuple):
            return tuple(str(item) for item in chunk[0]), chunk[2]
    return (), chunk


def _tool_output_text(message: ToolMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content or "")


def browser_action_payload(
    namespace: Sequence[str],
    agent_name: str,
    tool_name: str,
    message: ToolMessage,
    *,
    screenshot_id: str | None = None,
) -> dict[str, Any]:
    content = _tool_output_text(message)
    stripped = content.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    payload = {
        "agent": ", ".join(namespace) or agent_name,
        "tool": tool_name,
        "summary": " ".join(first_line.split())[:240],
        "ok": not content.lstrip().startswith("Error"),
    }
    if screenshot_id:
        payload["screenshot_id"] = screenshot_id
    return payload


def _save_screenshot(
    message: ToolMessage, *, workspace_path: str, session_id: str, run_id: str
) -> str | None:
    if not workspace_path or not session_id or not run_id:
        return None
    content = message.content
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image_url":
            continue
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            continue
        try:
            data = base64.b64decode(url.split(",", 1)[1], validate=True)
        except Exception:
            logger.warning("invalid screenshot payload session=%s run=%s", session_id, run_id)
            return None
        if len(data) > settings.browser_screenshot_max_bytes:
            logger.warning(
                "screenshot too large session=%s run=%s bytes=%s",
                session_id,
                run_id,
                len(data),
            )
            return None
        try:
            root = resolve_workspace_path(workspace_path, create=True)
        except WorkspaceError:
            logger.exception("cannot resolve workspace for screenshot session=%s", session_id)
            return None
        screenshot_id = uuid.uuid4().hex
        target = root / "screenshots" / session_id / run_id / f"{screenshot_id}.png"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError:
            logger.exception("failed to save screenshot session=%s run=%s", session_id, run_id)
            return None
        return screenshot_id
    return None


def build_backend(workspace_path: str, *, skills_dir: Path | str | None = None):
    from deepagents.backends import CompositeBackend, FilesystemBackend

    root = resolve_workspace_path(workspace_path, create=True)
    workspace_backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    if skills_dir is None:
        return workspace_backend
    skills_root = Path(skills_dir).expanduser().resolve()
    return CompositeBackend(
        default=workspace_backend,
        routes={"/skills/": FilesystemBackend(root_dir=skills_root, virtual_mode=True)},
    )


class DeepAgentExecutor:
    def __init__(
        self,
        *,
        provider: Any,
        checkpoint_dir: Path,
        agent_name: str,
        memory_context: str = "",
        workspace_path: str = "",
        enabled_skills: Sequence[str] = (),
        skill_tools: SkillToolRegistry | None = None,
        skills_dir: Path | None = None,
        run_id: str = "",
    ) -> None:
        self.provider = provider
        self.checkpoint_dir = checkpoint_dir
        self.agent_name = agent_name
        self.memory_context = memory_context
        self.workspace_path = workspace_path
        self.enabled_skills = tuple(enabled_skills)
        self.skill_tools = skill_tools if skill_tools is not None else SKILL_TOOL_REGISTRY
        self.skills_dir = skills_dir
        self.run_id = run_id

    async def run(self, *, instructions: str, session_id: str, content: str) -> AsyncIterator[ExecutorEvent]:
        try:
            from deepagents import create_deep_agent
            from langchain.agents.middleware import ModelRetryMiddleware
            from langchain_openai import ChatOpenAI
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.errors import GraphRecursionError

            from app.services.skill_middleware import AgentSkillsMiddleware
        except Exception as exc:  # pragma: no cover - import guard
            yield ExecutorEvent("run_failed", {"error": {"type": type(exc).__name__, "message": str(exc)}})
            return

        if not self.provider.api_key:
            yield ExecutorEvent(
                "run_failed",
                {"error": {"type": "ProviderError", "message": "provider api_key is not configured"}},
            )
            return

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = self.checkpoint_dir / f"{session_id}.sqlite"
        collector = UsageCollector()
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        output_parts: list[str] = []
        step_limit_output: str | None = None
        browser = BrowserSession()

        try:
            chat_model = ChatOpenAI(
                api_key=self.provider.api_key,
                base_url=self.provider.base_url,
                model=self.provider.model,
                temperature=0.7,
                stream_usage=True,
                max_retries=0,
            )
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
                system_prompt = instructions
                if self.memory_context:
                    system_prompt = f"{system_prompt}\n\n{self.memory_context}"
                backend = build_backend(
                    self.workspace_path,
                    skills_dir=(
                        self.skills_dir if self.skills_dir is not None else settings.skills_dir
                    ),
                )
                skills_middleware = AgentSkillsMiddleware(
                    backend=backend,
                    sources=[("/skills/", "Platform")],
                    enabled_skills=self.enabled_skills,
                    tools=self.skill_tools.build(
                        SkillToolContext(
                            api_key=self.provider.api_key,
                            base_url=self.provider.base_url,
                            browser=browser,
                        ),
                    ),
                )
                # One retry layer only: ChatOpenAI retries are disabled so this
                # middleware owns backoff. The default retry_on follows
                # ModelError.is_retryable (429/5xx/timeouts yes, 4xx no).
                retry_middleware = ModelRetryMiddleware(
                    max_retries=3,
                    retry_on=lambda exc: _log_retryable(
                        exc, agent_name=self.agent_name, session_id=session_id
                    ),
                    on_failure="error",
                    initial_delay=1.0,
                    backoff_factor=2.0,
                    max_delay=30.0,
                )
                agent = create_deep_agent(
                    model=chat_model,
                    tools=[],
                    system_prompt=system_prompt,
                    checkpointer=checkpointer,
                    backend=backend,
                    name=self.agent_name,
                    skills=["/skills/"],
                    middleware=[skills_middleware, retry_middleware],
                )
                config = {
                    "configurable": {"thread_id": session_id},
                    "callbacks": [collector],
                    "recursion_limit": settings.run_recursion_limit,
                }
                input_state = {
                    "messages": [HumanMessage(content=content)],
                    "skills_metadata": None,
                }
                continuations = 0
                while True:
                    try:
                        async for event in self._stream_events(
                            agent, input_state, config, session_id=session_id
                        ):
                            if event.type == "assistant_delta":
                                output_parts.append(str(event.payload.get("delta") or ""))
                            yield event
                        break
                    except GraphRecursionError as exc:
                        if continuations < MAX_STEP_LIMIT_CONTINUATIONS:
                            continuations += 1
                            logger.warning(
                                "run hit step limit; continuing on the same thread agent=%s session=%s limit=%s",
                                self.agent_name,
                                session_id,
                                settings.run_recursion_limit,
                            )
                            yield ExecutorEvent(
                                "step_limit",
                                {
                                    "limit": settings.run_recursion_limit,
                                    "action": "continue",
                                    "continuation": continuations,
                                },
                            )
                            input_state = {
                                "messages": [HumanMessage(content=STEP_LIMIT_CONTINUE_PROMPT)]
                            }
                            continue
                        logger.warning(
                            "run hit step limit again; summarizing agent=%s session=%s limit=%s",
                            self.agent_name,
                            session_id,
                            settings.run_recursion_limit,
                        )
                        try:
                            summary = await self._answer_after_step_limit(
                                agent=agent,
                                config=config,
                                chat_model=chat_model,
                                system_prompt=system_prompt,
                                collector=collector,
                            )
                        except Exception:
                            logger.exception("step-limit summary failed")
                            raise exc from None
                        yield ExecutorEvent(
                            "step_limit",
                            {"limit": settings.run_recursion_limit, "action": "summary"},
                        )
                        step_limit_output = (
                            f"{STEP_LIMIT_NOTICE}\n\n{summary}" if summary else STEP_LIMIT_NOTICE
                        )
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield ExecutorEvent(
                "run_failed",
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )
            return
        finally:
            try:
                cleanup = asyncio.ensure_future(browser.aclose())
                _BROWSER_CLEANUP_TASKS.add(cleanup)
                cleanup.add_done_callback(_BROWSER_CLEANUP_TASKS.discard)
                await asyncio.wait_for(asyncio.shield(cleanup), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("failed to close browser session")

        for entry in collector.entries:
            usage["input_tokens"] += int(entry.get("input_tokens") or 0)
            usage["output_tokens"] += int(entry.get("output_tokens") or 0)
            usage["total_tokens"] += int(entry.get("total_tokens") or 0)
            yield ExecutorEvent("model_usage", entry)

        output = (
            step_limit_output
            if step_limit_output is not None
            else ("".join(output_parts).strip() or "（无回复）")
        )
        yield ExecutorEvent(
            "run_completed",
            {
                "output": output,
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
            },
        )

    async def _stream_events(
        self,
        agent: Any,
        input_state: dict[str, Any],
        config: dict[str, Any],
        *,
        session_id: str,
    ) -> AsyncIterator[ExecutorEvent]:
        seen_tool_results: set[str] = set()
        tool_names_by_call_id: dict[str, str] = {}
        async for chunk in agent.astream(
            input_state,
            config=config,
            stream_mode="messages",
            subgraphs=True,
        ):
            namespace, data = _split_stream_chunk(chunk)
            if not (isinstance(data, tuple) and len(data) >= 2):
                continue
            message = data[0]
            if isinstance(message, ToolMessage) and not isinstance(message, ToolMessageChunk):
                call_id = str(getattr(message, "tool_call_id", "") or "")
                tool_name = str(getattr(message, "name", "") or "") or (
                    tool_names_by_call_id.get(call_id, "")
                )
                dedupe_key = call_id or str(id(message))
                if tool_name in BROWSER_TOOL_NAMES and dedupe_key not in seen_tool_results:
                    seen_tool_results.add(dedupe_key)
                    screenshot_id = (
                        _save_screenshot(
                            message,
                            workspace_path=self.workspace_path,
                            session_id=session_id,
                            run_id=self.run_id,
                        )
                        if tool_name == "browser_screenshot"
                        else None
                    )
                    yield ExecutorEvent(
                        "browser_action",
                        browser_action_payload(
                            namespace,
                            self.agent_name,
                            tool_name,
                            message,
                            screenshot_id=screenshot_id,
                        ),
                    )
                continue
            if not isinstance(message, AIMessageChunk):
                continue

            for item in getattr(message, "tool_calls", None) or []:
                if not isinstance(item, dict):
                    continue
                call_id = str(item.get("id") or "")
                call_name = str(item.get("name") or "")
                if call_id and call_name:
                    tool_names_by_call_id[call_id] = call_name

            tool_names = [
                str(item.get("name"))
                for item in (getattr(message, "tool_call_chunks", None) or [])
                if isinstance(item, dict) and item.get("name")
            ]
            if tool_names:
                yield ExecutorEvent(
                    "agent_update",
                    {"agent": ", ".join(namespace) or self.agent_name, "tool_calls": tool_names},
                )

            text = _message_text(message)
            if not text:
                continue
            if namespace:
                yield ExecutorEvent(
                    "subagent_response",
                    {"agent": ", ".join(namespace), "content": text},
                )
            else:
                yield ExecutorEvent("assistant_delta", {"delta": text})

    async def _answer_after_step_limit(
        self,
        *,
        agent: Any,
        config: dict[str, Any],
        chat_model: Any,
        system_prompt: str,
        collector: UsageCollector,
    ) -> str:
        messages: list[Any] = []
        try:
            state = await agent.aget_state(config)
            messages = list((getattr(state, "values", None) or {}).get("messages") or [])
        except Exception:
            logger.exception("failed to read checkpoint state after step limit")
        messages = _drop_dangling_tool_calls(messages)
        response = await chat_model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                *messages,
                HumanMessage(content=STEP_LIMIT_FINAL_PROMPT),
            ],
            config={"callbacks": [collector]},
        )
        return _content_text(getattr(response, "content", "")).strip()
