import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from server.domain.agent_config import ModelDefinition, ModelProvider
from server.domain.run_approval import (
    RunApprovalAction,
    RunApprovalInterrupt,
    RunApprovalRequest,
    RunApprovalResume,
)
from server.domain.run_events import (
    DeepAgentGraphUpdatePayload,
    DeepAgentMessageDeltaPayload,
    DeepAgentSubagentResponsePayload,
    RunEventPayload,
    ModelUsagePayload,
    RunEventType,
    StreamFallbackPayload,
)
from server.infrastructure.agent_filesystem_backend import (
    AGENT_WORKSPACE_DIRECTORIES,
    AgentFilesystemBackend,
)
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


MEMORY_INDEX_PATH = "/memories/AGENTS.md"
GENERAL_PURPOSE_SKILL_PROMPT = (
    "Access a skill only when the delegated task explicitly specifies that skill; "
    "otherwise, do not access any skills."
)


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


@dataclass(frozen=True)
class DeepAgentStreamEvent:
    type: RunEventType
    payload: RunEventPayload


@dataclass(frozen=True)
class _LangGraphStreamPart:
    namespace: tuple[str, ...]
    mode: str
    data: Any


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
        resume: RunApprovalResume | None = None,
    ) -> str | RunApprovalRequest:
        if not messages and resume is None:
            raise ValueError("messages are required")
        try:
            from deepagents import create_deep_agent
            from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
            from langchain_core.messages import AIMessage, HumanMessage
        except Exception as exc:
            raise RuntimeError("DeepAgent runtime requires the deepagents package") from exc

        self._agent_workspace.mkdir(parents=True, exist_ok=True)
        for directory in AGENT_WORKSPACE_DIRECTORIES:
            (self._agent_workspace / directory).mkdir(parents=True, exist_ok=True)
        general_purpose_subagent = dict(GENERAL_PURPOSE_SUBAGENT)
        default_subagent_prompt = str(general_purpose_subagent.get("system_prompt") or "").strip()
        general_purpose_subagent["system_prompt"] = (
            f"{default_subagent_prompt}\n\n{GENERAL_PURPOSE_SKILL_PROMPT}"
        )
        create_kwargs: dict[str, Any] = {
            "tools": build_platform_tools(
                options.tools,
                context_workspace=self._context_workspace,
                schedule_service=self._schedule_service,
                tool_context=self._tool_context,
            ),
            "model": self._chat_model(),
            "system_prompt": instructions.strip(),
            "backend": AgentFilesystemBackend(root_dir=self._agent_workspace, virtual_mode=True),
            "skills": ["/skills/"],
            "subagents": [general_purpose_subagent],
        }
        if options.use_longterm_memory:
            create_kwargs["memory"] = [MEMORY_INDEX_PATH]
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
        if resume is not None:
            try:
                from langgraph.types import Command
            except Exception as exc:
                raise RuntimeError("DeepAgent resume requires langgraph") from exc
            input_state: Any = Command(resume=resume.to_command_value())
        else:
            input_messages = _to_langchain_messages(messages, HumanMessage, AIMessage, self._model.provider)
            input_state = {"messages": input_messages}
        invoke_config = _invoke_config(options, assistant_id=self._agent_id, thread_id=thread_id)
        if stream_callback is not None:
            invoke_config["callbacks"] = [_usage_callback(stream_callback, self._model.model)]
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
        approval = _approval_request_from_stream_data(result)
        if approval is not None:
            return approval
        return self._extract_content(result)

    async def _run_agent(
        self,
        agent: Any,
        input_state: Any,
        invoke_config: dict[str, Any],
        *,
        stream_callback: Callable[[DeepAgentStreamEvent], None] | None,
    ) -> Any:
        if stream_callback is None or not hasattr(agent, "astream"):
            result = await agent.ainvoke(input_state, config=invoke_config)
            return _approval_request_from_stream_data(result) or result

        final_result: Any = None
        stream_started = False
        subagent_names: dict[tuple[str, ...], str] = {}
        subagent_responses: set[str] = set()
        try:
            async for chunk in agent.astream(
                input_state,
                config=invoke_config,
                stream_mode=["messages", "updates", "values"],
                subgraphs=True,
            ):
                stream_started = True
                part = _split_langgraph_stream_part(chunk)
                approval = _approval_request_from_stream_data(part.data)
                if approval is not None:
                    return approval
                if part.namespace:
                    if part.mode == "messages":
                        agent_name = _message_agent_name(part.data)
                        if agent_name:
                            subagent_names[part.namespace] = agent_name
                    elif part.mode == "updates":
                        event = _subagent_response_event(
                            part.data,
                            namespace=part.namespace,
                            agent=subagent_names.get(part.namespace, ""),
                        )
                        if event:
                            if isinstance(event.payload, DeepAgentSubagentResponsePayload):
                                subagent_responses.add(event.payload.content)
                            _emit_stream_event(stream_callback, event)
                    continue
                if part.mode == "messages":
                    event = _message_delta_event(part.data)
                    if event:
                        _emit_stream_event(stream_callback, event)
                    continue
                if part.mode == "updates":
                    event = _update_event(part.data, suppressed_previews=subagent_responses)
                    if event:
                        _emit_stream_event(stream_callback, event)
                    continue
                if part.mode == "values":
                    final_result = part.data
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
            stream_usage=True,
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


def _split_langgraph_stream_part(chunk: Any) -> _LangGraphStreamPart:
    if isinstance(chunk, dict):
        stream_type = chunk.get("type")
        if stream_type in {"messages", "updates", "values"} and "data" in chunk:
            raw_namespace = chunk.get("namespace") or ()
            namespace = tuple(str(item) for item in raw_namespace) if isinstance(raw_namespace, list | tuple) else ()
            return _LangGraphStreamPart(namespace=namespace, mode=str(stream_type), data=chunk["data"])
    if (
        isinstance(chunk, tuple)
        and len(chunk) == 3
        and isinstance(chunk[0], tuple)
        and isinstance(chunk[1], str)
    ):
        return _LangGraphStreamPart(
            namespace=tuple(str(item) for item in chunk[0]),
            mode=chunk[1],
            data=chunk[2],
        )
    if isinstance(chunk, tuple) and len(chunk) == 2 and isinstance(chunk[0], str):
        return _LangGraphStreamPart(namespace=(), mode=chunk[0], data=chunk[1])
    return _LangGraphStreamPart(namespace=(), mode="values", data=chunk)


def _emit_stream_event(callback: Callable[[DeepAgentStreamEvent], None], event: DeepAgentStreamEvent) -> None:
    try:
        callback(event)
    except Exception:
        return


def _message_delta_event(data: Any) -> DeepAgentStreamEvent | None:
    message, metadata = _message_and_metadata(data)
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


def _message_and_metadata(data: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(data, tuple) and len(data) == 2:
        message, raw_metadata = data
        return message, raw_metadata if isinstance(raw_metadata, dict) else {}
    return data, {}


def _message_agent_name(data: Any) -> str:
    _, metadata = _message_and_metadata(data)
    return str(metadata.get("lc_agent_name") or metadata.get("checkpoint_ns") or "").strip()


def _subagent_response_event(
    data: Any,
    *,
    namespace: tuple[str, ...],
    agent: str,
) -> DeepAgentStreamEvent | None:
    node, message = _latest_ai_message(data)
    if message is None:
        return None
    content = _content_to_text(getattr(message, "content", None)).strip()
    if not content:
        return None
    return DeepAgentStreamEvent(
        RunEventType.SUBAGENT_RESPONSE,
        DeepAgentSubagentResponsePayload(
            content=content,
            namespace=namespace,
            agent=agent,
            node=node,
            source_class=message.__class__.__name__,
        ),
    )


def _latest_ai_message(value: Any) -> tuple[str, Any | None]:
    if not isinstance(value, dict):
        return "", None
    messages = value.get("messages")
    if isinstance(messages, list) and messages:
        message = messages[-1]
        if getattr(message, "type", "") == "ai":
            return "", message
    for key, item in reversed(tuple(value.items())):
        node, message = _latest_ai_message(item)
        if message is not None:
            return node or str(key), message
    return "", None


def _update_event(data: Any, *, suppressed_previews: set[str] | None = None) -> DeepAgentStreamEvent | None:
    if not isinstance(data, dict) or not data:
        return None
    nodes = tuple(str(key) for key in data.keys())
    preview = _content_to_text(_latest_message_content(data))
    if preview.strip() in (suppressed_previews or set()):
        preview = ""
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


def _invoke_config(options: DeepAgentRuntimeOptions, *, assistant_id: str, thread_id: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "recursion_limit": options.max_iterations,
        "metadata": {"assistant_id": assistant_id},
    }
    normalized_thread_id = thread_id.strip()
    if normalized_thread_id:
        config["configurable"] = {"thread_id": normalized_thread_id}
    return config


def _normalize_interrupt_on(value: Any) -> dict[str, dict[str, list[str]]] | None:
    if isinstance(value, dict):
        return {
            str(key): {"allowed_decisions": ["approve", "reject"]}
            for key, item in value.items()
            if str(key).strip() and bool(item)
        }
    if isinstance(value, list | tuple):
        return {
            str(item).strip(): {"allowed_decisions": ["approve", "reject"]}
            for item in value
            if str(item).strip()
        }
    return None


def _approval_request_from_stream_data(data: Any) -> RunApprovalRequest | None:
    if isinstance(data, RunApprovalRequest):
        return data
    if not isinstance(data, dict):
        return None
    raw_interrupts = data.get("__interrupt__")
    if not isinstance(raw_interrupts, list | tuple):
        return None
    interrupts: list[RunApprovalInterrupt] = []
    for raw_interrupt in raw_interrupts:
        interrupt_id = str(getattr(raw_interrupt, "id", "") or "").strip()
        value = getattr(raw_interrupt, "value", None)
        request = value if isinstance(value, dict) else {}
        raw_actions = request.get("action_requests")
        raw_configs = request.get("review_configs")
        configs = {
            str(item.get("action_name") or ""): item
            for item in (raw_configs if isinstance(raw_configs, list | tuple) else ())
            if isinstance(item, dict)
        }
        actions: list[RunApprovalAction] = []
        for raw_action in raw_actions if isinstance(raw_actions, list | tuple) else ():
            action = raw_action if isinstance(raw_action, dict) else {}
            name = str(action.get("name") or "").strip()
            if not name:
                continue
            raw_allowed = configs.get(name, {}).get("allowed_decisions")
            allowed = tuple(
                decision
                for item in (raw_allowed if isinstance(raw_allowed, list | tuple) else ())
                if (decision := str(item).strip()) in {"approve", "reject"}
            )
            actions.append(
                RunApprovalAction(
                    name=name,
                    args=dict(action.get("args") or {}) if isinstance(action.get("args"), dict) else {},
                    description=str(action.get("description") or ""),
                    allowed_decisions=allowed or ("approve", "reject"),
                )
            )
        if interrupt_id and actions:
            interrupts.append(RunApprovalInterrupt(interrupt_id=interrupt_id, actions=tuple(actions)))
    return RunApprovalRequest(interrupts=tuple(interrupts)) if interrupts else None


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


def _usage_callback(callback: Callable[[DeepAgentStreamEvent], None], model_name: str):
    from langchain_core.callbacks import AsyncCallbackHandler

    class UsageCallback(AsyncCallbackHandler):
        # Persist on the event loop before the next model call / terminal transition.
        run_inline = True
        raise_error = True

        async def on_llm_end(self, response, *, run_id, **kwargs):
            usage = None
            actual_model = model_name
            for generations in response.generations:
                for generation in generations:
                    message = getattr(generation, "message", None)
                    metadata = getattr(message, "response_metadata", {}) or {}
                    actual_model = metadata.get("model_name") or metadata.get("model") or actual_model
                    candidate = getattr(message, "usage_metadata", None)
                    if candidate is not None:
                        usage = candidate
                        break
                if usage is not None:
                    break
            if usage is None:
                raw = (response.llm_output or {}).get("token_usage")
                if isinstance(raw, dict):
                    usage = {"input_tokens": raw.get("prompt_tokens"), "output_tokens": raw.get("completion_tokens")}
            def count(value):
                return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            callback(DeepAgentStreamEvent(RunEventType.MODEL_USAGE, ModelUsagePayload(
                call_id=str(run_id), model=str(actual_model),
                input_tokens=count((usage or {}).get("input_tokens")),
                output_tokens=count((usage or {}).get("output_tokens")),
                cached_input_tokens=count(((usage or {}).get("input_token_details") or {}).get("cache_read")) or 0,
            )))

        async def on_llm_error(self, error, *, run_id, **kwargs):
            callback(DeepAgentStreamEvent(RunEventType.MODEL_USAGE, ModelUsagePayload(
                call_id=str(run_id), model=model_name, error=True,
            )))

    return UsageCallback()
