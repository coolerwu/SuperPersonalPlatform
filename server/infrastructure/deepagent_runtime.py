import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from server.domain.agent_config import ModelDefinition, ModelProvider
from server.domain.run_events import (
    DeepAgentGraphUpdatePayload,
    DeepAgentMessageDeltaPayload,
    RunEventPayload,
    RunEventType,
    StreamFallbackPayload,
)
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


MEMORY_INDEX_PATH = "/memories/AGENTS.md"
DEFAULT_MEMORY_INDEX = """# Memory Index

This file is loaded automatically as the agent's long-term memory index.

## Stable Preferences

- Add durable user preferences and collaboration rules here.
- Do not store passwords, API keys, access tokens, or other credentials.

## References

- Store larger or task-specific notes in sibling files or subdirectories under `/memories/`.
- When a detail is not present here, use `ls` and `read_file` to inspect `/memories/` before assuming it is unknown.
"""

LONGTERM_MEMORY_PROMPT = """## Platform Memory Boundary

DeepAgent memory is loaded from `/memories/AGENTS.md`. Follow the injected memory guidelines for saving agent-specific memory.
Do not use `write_context` for personal memory, user preferences, future conversation rules, or "remember this" requests.

Use `write_context` only when the user explicitly asks to save shared knowledge, documentation, reference material, or knowledge-base content under workspace/context/knowledge/files.

When the user asks to look up notes, recent notes, synced documents, WebDAV files, knowledge-base content, or notebook entries, call `search_context` first. `/memories/...` is only your own long-term memory, not the user's synced notebook.
"""

BROWSER_RESEARCH_PROMPT = """## Platform Browser Research

When the user asks for current, recent, latest, news, open-web, or source-backed information, use `browser_search` first to discover source URLs, then call `browser_extract` on the most relevant result URLs before making claims.
Use `browser_search` for discovery only; use `browser_extract` to read page content. Cite the source URLs you used in the answer.
If browser search or extraction is blocked by a login, captcha, verification page, or anti-bot page, say that browser authorization or manual source text is needed instead of guessing.
"""

RHYTHMIC_DELIVERY_PROMPT = """## Rhythmic Delivery Middleware

When the current task asks you to produce multiple messages for scheduled or paced delivery, write each deliverable message inside its own XML-style block:

<delivery-item>
message text
</delivery-item>

Do not put introductions, summaries, or extra prose outside the delivery-item blocks unless the user specifically asked for a single combined response. Each block should be readable as a standalone message.
"""


@dataclass(frozen=True)
class RuntimeAttachment:
    type: str
    mime: str
    path: Path
    filename: str = ""

    @property
    def is_image(self) -> bool:
        return self.type == "image" and self.mime.startswith("image/")


@dataclass(frozen=True)
class RuntimeMessage:
    role: str
    content: str
    attachments: tuple[RuntimeAttachment, ...] = ()

    @property
    def has_images(self) -> bool:
        return any(attachment.is_image for attachment in self.attachments)


@dataclass(frozen=True)
class DeepAgentRuntimeOptions:
    max_iterations: int = 60
    name: str = ""
    debug: bool = False
    todo_list: bool = True
    filesystem_enabled: bool = False
    use_longterm_memory: bool = True
    tools: tuple[str, ...] = ()
    interrupt_on: tuple[str, ...] = ()
    middleware: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeepAgentStreamEvent:
    type: RunEventType
    payload: RunEventPayload


class DeepAgentRuntime:
    def __init__(
        self,
        model: ModelDefinition,
        *,
        context_workspace: Path,
        agent_workspace: Path,
        schedule_service: Any = None,
        tool_context: PlatformToolContext | None = None,
    ) -> None:
        self._model = model
        self._context_workspace = context_workspace
        self._agent_workspace = agent_workspace
        self._agent_id = agent_workspace.name
        self._schedule_service = schedule_service
        self._tool_context = tool_context

    async def run(
        self,
        *,
        instructions: str,
        messages: tuple[RuntimeMessage, ...],
        options: DeepAgentRuntimeOptions,
        checkpoint_path: Path | None = None,
        thread_id: str = "",
        stream_callback: Callable[[DeepAgentStreamEvent], None] | None = None,
    ) -> str:
        if not messages:
            raise ValueError("messages are required")
        try:
            from deepagents import create_deep_agent
            from deepagents.backends import FilesystemBackend
            from langchain_core.messages import AIMessage, HumanMessage
        except Exception as exc:
            raise RuntimeError("DeepAgent runtime requires the deepagents package") from exc

        self._agent_workspace.mkdir(parents=True, exist_ok=True)
        (self._agent_workspace / "skills").mkdir(parents=True, exist_ok=True)
        (self._agent_workspace / "memories").mkdir(parents=True, exist_ok=True)
        (self._agent_workspace / "improvements").mkdir(parents=True, exist_ok=True)
        memory_sources = _longterm_memory_sources(self._agent_workspace, options)
        create_kwargs: dict[str, Any] = {
            "tools": build_platform_tools(
                options.tools,
                context_workspace=self._context_workspace,
                schedule_service=self._schedule_service,
                tool_context=self._tool_context,
            ),
            "model": self._chat_model(),
            "system_prompt": _runtime_instructions(instructions, options),
            "backend": FilesystemBackend(root_dir=self._agent_workspace, virtual_mode=True),
            "skills": ["/skills/"],
        }
        if memory_sources:
            create_kwargs["memory"] = memory_sources
        name = options.name.strip()
        if name:
            create_kwargs["name"] = name
        if options.debug:
            create_kwargs["debug"] = True
        interrupt_on = _normalize_interrupt_on(options.interrupt_on)
        if interrupt_on:
            create_kwargs["interrupt_on"] = interrupt_on
        middleware = _deepagent_builtin_middleware(create_deep_agent, options)
        if middleware:
            create_kwargs["middleware"] = middleware
        input_messages = _to_langchain_messages(messages, HumanMessage, AIMessage, self._model.provider)
        input_state: dict[str, Any] = {"messages": input_messages}
        invoke_config = _invoke_config(options, assistant_id=self._agent_id, thread_id=thread_id)
        if checkpoint_path is not None and thread_id.strip():
            try:
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            except Exception as exc:
                raise RuntimeError("DeepAgent checkpointing requires langgraph-checkpoint-sqlite") from exc
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
                create_kwargs["checkpointer"] = checkpointer
                agent = create_deep_agent(**create_kwargs)
                result = await self._run_agent(agent, input_state, invoke_config, stream_callback=stream_callback)
        else:
            agent = create_deep_agent(**create_kwargs)
            result = await self._run_agent(agent, input_state, invoke_config, stream_callback=stream_callback)
        return self._extract_content(result)

    async def _run_agent(
        self,
        agent: Any,
        input_state: dict[str, Any],
        invoke_config: dict[str, Any],
        *,
        stream_callback: Callable[[DeepAgentStreamEvent], None] | None,
    ) -> Any:
        if stream_callback is None or not hasattr(agent, "astream"):
            return await agent.ainvoke(input_state, config=invoke_config)

        final_result: Any = None
        stream_started = False
        try:
            async for chunk in agent.astream(
                input_state,
                config=invoke_config,
                stream_mode=["messages", "updates", "values"],
            ):
                stream_started = True
                mode, data = _split_langgraph_stream_part(chunk)
                if mode == "messages":
                    event = _message_delta_event(data)
                    if event:
                        _emit_stream_event(stream_callback, event)
                    continue
                if mode == "updates":
                    event = _update_event(data)
                    if event:
                        _emit_stream_event(stream_callback, event)
                    continue
                if mode == "values":
                    final_result = data
        except TypeError as exc:
            if stream_started:
                raise
            _emit_stream_event(
                stream_callback,
                DeepAgentStreamEvent(
                    RunEventType.STREAM_FALLBACK,
                    StreamFallbackPayload(message="agent stream unsupported; falling back to ainvoke", error=str(exc)),
                ),
            )
            return await agent.ainvoke(input_state, config=invoke_config)

        if final_result is not None:
            return final_result
        _emit_stream_event(
            stream_callback,
            DeepAgentStreamEvent(
                RunEventType.STREAM_FALLBACK,
                StreamFallbackPayload(message="agent stream returned no final state; falling back to ainvoke"),
            ),
        )
        return await agent.ainvoke(input_state, config=invoke_config)

    def _chat_model(self):
        model = self._model
        if model.provider is ModelProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            kwargs: dict[str, object] = {
                "api_key": model.api_key,
                "model": model.model,
                "temperature": model.temperature if model.temperature is not None else 0.7,
            }
            if model.base_url.strip():
                kwargs["base_url"] = model.base_url
            return ChatAnthropic(**kwargs)

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=model.api_key,
            base_url=model.base_url,
            model=model.model,
            temperature=model.temperature if model.temperature is not None else 0.7,
        )

    def _extract_content(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            messages = result.get("messages")
            if isinstance(messages, list) and messages:
                content = getattr(messages[-1], "content", None)
                if content is not None:
                    return str(content)
            for key in ("output", "content", "answer", "response"):
                if key in result:
                    return str(result[key])
        return str(result)


def _split_langgraph_stream_part(chunk: Any) -> tuple[str, Any]:
    if isinstance(chunk, dict):
        stream_type = chunk.get("type")
        if stream_type in {"messages", "updates", "values"} and "data" in chunk:
            return str(stream_type), chunk["data"]
    if isinstance(chunk, tuple) and len(chunk) == 2 and isinstance(chunk[0], str):
        return chunk[0], chunk[1]
    return "values", chunk


def _emit_stream_event(callback: Callable[[DeepAgentStreamEvent], None], event: DeepAgentStreamEvent) -> None:
    try:
        callback(event)
    except Exception:
        return


def _message_delta_event(data: Any) -> DeepAgentStreamEvent | None:
    message = data
    metadata: dict[str, Any] = {}
    if isinstance(data, tuple) and len(data) == 2:
        message, raw_metadata = data
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
    text = _content_to_text(getattr(message, "content", message))
    if not text:
        return None
    node = str(metadata.get("langgraph_node") or "").strip()
    agent_name = str(metadata.get("lc_agent_name") or metadata.get("checkpoint_ns") or "").strip()
    return DeepAgentStreamEvent(
        RunEventType.ASSISTANT_DELTA,
        DeepAgentMessageDeltaPayload(
            delta=text,
            node=node,
            agent=agent_name,
            source_class=message.__class__.__name__,
        ),
    )


def _update_event(data: Any) -> DeepAgentStreamEvent | None:
    if not isinstance(data, dict) or not data:
        return None
    nodes = tuple(str(key) for key in data.keys())
    preview = _content_to_text(_latest_message_content(data))
    return DeepAgentStreamEvent(
        RunEventType.AGENT_UPDATE,
        DeepAgentGraphUpdatePayload(
            nodes=nodes,
            preview=preview[-500:] if preview else "",
            source_class=data.__class__.__name__,
        ),
    )


def _latest_message_content(value: Any) -> Any:
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list) and messages:
            return getattr(messages[-1], "content", messages[-1])
        for item in value.values():
            content = _latest_message_content(item)
            if content:
                return content
    return None


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _runtime_instructions(instructions: str, options: DeepAgentRuntimeOptions) -> str:
    sections = [instructions.strip()]
    if "browser_extract" in options.tools:
        sections.append(BROWSER_RESEARCH_PROMPT)
    if options.use_longterm_memory:
        sections.append(LONGTERM_MEMORY_PROMPT)
    if "rhythmic_delivery" in options.middleware:
        sections.append(RHYTHMIC_DELIVERY_PROMPT)
    return "\n\n".join(section for section in sections if section).strip()


def _invoke_config(options: DeepAgentRuntimeOptions, *, assistant_id: str, thread_id: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "recursion_limit": options.max_iterations,
        "metadata": {"assistant_id": assistant_id},
    }
    normalized_thread_id = thread_id.strip()
    if normalized_thread_id:
        config["configurable"] = {"thread_id": normalized_thread_id}
    return config


def _longterm_memory_sources(agent_workspace: Path, options: DeepAgentRuntimeOptions) -> list[str]:
    if not options.use_longterm_memory:
        return []
    memory_index = agent_workspace / "memories" / "AGENTS.md"
    if not memory_index.exists():
        memory_index.parent.mkdir(parents=True, exist_ok=True)
        memory_index.write_text(DEFAULT_MEMORY_INDEX, encoding="utf-8")
    return [MEMORY_INDEX_PATH]


def _normalize_interrupt_on(value: Any) -> dict[str, bool] | None:
    if isinstance(value, dict):
        return {str(key): bool(item) for key, item in value.items() if str(key).strip()}
    if isinstance(value, list):
        return {str(item).strip(): True for item in value if str(item).strip()}
    return None


def _deepagent_builtin_middleware(create_deep_agent: Any, options: DeepAgentRuntimeOptions) -> list[Any]:
    middleware: list[Any] = []
    if options.todo_list and not _create_deep_agent_has_default_middleware(create_deep_agent, "TodoListMiddleware"):
        try:
            from langchain.agents.middleware.todo import TodoListMiddleware
        except Exception:
            TodoListMiddleware = None
        if TodoListMiddleware is not None:
            middleware.append(TodoListMiddleware())
    from server.infrastructure.skill_improvement_middleware import SkillImprovementMiddleware

    middleware.append(SkillImprovementMiddleware())
    return middleware


def _create_deep_agent_has_default_middleware(create_deep_agent: Any, name: str) -> bool:
    try:
        import inspect

        return f"{name}(" in inspect.getsource(create_deep_agent)
    except Exception:
        return False


def _to_langchain_messages(
    messages: tuple[RuntimeMessage, ...],
    human_cls: Any,
    ai_cls: Any,
    provider: ModelProvider = ModelProvider.OPENAI_COMPATIBLE,
) -> list[Any]:
    result: list[Any] = []
    for message in messages:
        content = message.content.strip()
        attachments = tuple(attachment for attachment in message.attachments if attachment.is_image)
        if not content and not attachments:
            continue
        role = message.role.lower()
        if role in {"assistant", "ai"}:
            result.append(ai_cls(content=content))
        elif role in {"user", "human"}:
            result.append(human_cls(content=_message_content(content, attachments, provider)))
    return result


def _message_content(
    text: str,
    attachments: tuple[RuntimeAttachment, ...],
    provider: ModelProvider,
) -> str | list[dict[str, Any]]:
    if not attachments:
        return text

    if provider is ModelProvider.ANTHROPIC:
        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for attachment in attachments:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.mime,
                        "data": base64.b64encode(attachment.path.read_bytes()).decode("ascii"),
                    },
                }
            )
        return blocks

    blocks = []
    if text:
        blocks.append({"type": "text", "text": text})
    for attachment in attachments:
        data_url = f"data:{attachment.mime};base64,{base64.b64encode(attachment.path.read_bytes()).decode('ascii')}"
        blocks.append({"type": "image_url", "image_url": {"url": data_url}})
    return blocks


def load_agent_files(agent_workspace: Path, *, max_file_size: int = 512 * 1024) -> dict[str, dict[str, Any]]:
    agent_workspace.mkdir(parents=True, exist_ok=True)
    root = agent_workspace.resolve()
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(agent_workspace.rglob("*")):
        if not path.is_file() or _path_has_symlink(path, agent_workspace):
            continue
        if path.relative_to(agent_workspace).as_posix() == "memory/store.json":
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.stat().st_size > max_file_size:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        stat = path.stat()
        tool_path = "/" + path.relative_to(agent_workspace).as_posix()
        files[tool_path] = {
            "content": _split_file_content(content),
            "created_at": datetime.fromtimestamp(stat.st_ctime, UTC).isoformat(),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }
    return files


def persist_agent_files(agent_workspace: Path, files: Any) -> None:
    if not isinstance(files, dict):
        return
    agent_workspace.mkdir(parents=True, exist_ok=True)
    root = agent_workspace.resolve()
    for raw_path, raw_file in files.items():
        relative = _state_file_relative_path(str(raw_path))
        if relative is None or not isinstance(raw_file, dict):
            continue
        if relative.as_posix() == "memory/store.json":
            continue
        content = _state_file_content(raw_file.get("content"))
        if content is None:
            continue
        target = agent_workspace / relative
        if not _ensure_directory_within_root(target.parent, agent_workspace):
            continue
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(root):
            continue
        if target.exists() and _path_has_symlink(target, agent_workspace):
            continue
        target.write_text(content, encoding="utf-8")


def _state_file_relative_path(path: str) -> Path | None:
    if not path.startswith("/"):
        return None
    pure = PurePosixPath(path)
    if len(pure.parts) <= 1:
        return None
    if any(part in {"", ".", ".."} for part in pure.parts[1:]):
        return None
    return Path(*pure.parts[1:])


def _state_file_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join(value)
    return None


def _split_file_content(content: str, *, max_line_length: int = 2000) -> list[str]:
    lines = content.split("\n")
    result: list[str] = []
    for line in lines:
        if not line:
            result.append("")
            continue
        result.extend(line[index : index + max_line_length] for index in range(0, len(line), max_line_length))
    return result


def _path_has_symlink(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if root.is_symlink():
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _ensure_directory_within_root(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if root.is_symlink():
        return False
    current = root
    current.mkdir(parents=True, exist_ok=True)
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
        if current.exists() and not current.is_dir():
            return False
        current.mkdir(exist_ok=True)
    return True
