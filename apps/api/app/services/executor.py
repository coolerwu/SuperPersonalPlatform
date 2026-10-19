from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.services.workspace import resolve_workspace_path


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


def _message_text(message: AIMessageChunk) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _split_stream_chunk(chunk: Any) -> tuple[tuple[str, ...], Any]:
    if isinstance(chunk, tuple):
        if len(chunk) == 2 and isinstance(chunk[0], tuple):
            return tuple(str(item) for item in chunk[0]), chunk[1]
        if len(chunk) == 3 and isinstance(chunk[0], tuple):
            return tuple(str(item) for item in chunk[0]), chunk[2]
    return (), chunk


def build_backend(workspace_path: str):
    from deepagents.backends import FilesystemBackend

    root = resolve_workspace_path(workspace_path, create=True)
    return FilesystemBackend(root_dir=root, virtual_mode=True)


class DeepAgentExecutor:
    def __init__(
        self,
        *,
        provider: Any,
        checkpoint_dir: Path,
        agent_name: str,
        memory_context: str = "",
        workspace_path: str = "",
    ) -> None:
        self.provider = provider
        self.checkpoint_dir = checkpoint_dir
        self.agent_name = agent_name
        self.memory_context = memory_context
        self.workspace_path = workspace_path

    async def run(self, *, instructions: str, session_id: str, content: str) -> AsyncIterator[ExecutorEvent]:
        try:
            from deepagents import create_deep_agent
            from langchain_openai import ChatOpenAI
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
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

        try:
            chat_model = ChatOpenAI(
                api_key=self.provider.api_key,
                base_url=self.provider.base_url,
                model=self.provider.model,
                temperature=0.7,
                stream_usage=True,
            )
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
                system_prompt = instructions
                if self.memory_context:
                    system_prompt = f"{system_prompt}\n\n{self.memory_context}"
                backend = build_backend(self.workspace_path)
                agent = create_deep_agent(
                    model=chat_model,
                    tools=[],
                    system_prompt=system_prompt,
                    checkpointer=checkpointer,
                    backend=backend,
                    name=self.agent_name,
                )
                config = {
                    "configurable": {"thread_id": session_id},
                    "callbacks": [collector],
                    "recursion_limit": 50,
                }
                input_state = {"messages": [HumanMessage(content=content)]}
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
                    if not isinstance(message, AIMessageChunk):
                        continue

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
                        output_parts.append(text)
                        yield ExecutorEvent("assistant_delta", {"delta": text})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield ExecutorEvent(
                "run_failed",
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )
            return

        for entry in collector.entries:
            usage["input_tokens"] += int(entry.get("input_tokens") or 0)
            usage["output_tokens"] += int(entry.get("output_tokens") or 0)
            usage["total_tokens"] += int(entry.get("total_tokens") or 0)
            yield ExecutorEvent("model_usage", entry)

        output = "".join(output_parts).strip() or "（无回复）"
        yield ExecutorEvent(
            "run_completed",
            {
                "output": output,
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
            },
        )
