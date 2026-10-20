from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.skills import (
    SKILLS_SYSTEM_PROMPT,
    SkillMetadata,
    SkillsMiddleware,
    SkillsStateUpdate,
    _pin_update,
)
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _normalized_path(path: Any) -> str:
    return str(path or "").strip().lstrip("/")


def _last_summary_index(messages: Sequence[Any]) -> int | None:
    """Index of the most recent context-compaction summary message."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == "summarization"
        ):
            return index
    return None


def _skill_available_after(messages: Sequence[Any], start: int, path: str) -> bool:
    """Whether a read or pinned message for `path` still follows the summary."""
    target = _normalized_path(path)
    if not target:
        return False
    read_paths: dict[str, str] = {}
    for message in messages[start + 1 :]:
        if isinstance(message, AIMessage):
            for call in getattr(message, "tool_calls", None) or []:
                if not isinstance(call, dict) or call.get("name") != "read_file":
                    continue
                args = call.get("args") or {}
                file_path = args.get("file_path") if isinstance(args, dict) else None
                call_id = call.get("id")
                if isinstance(file_path, str) and call_id:
                    read_paths[str(call_id)] = file_path
        elif isinstance(message, ToolMessage) and getattr(message, "status", None) != "error":
            if _normalized_path(read_paths.get(str(message.tool_call_id))) == target:
                return True
        elif isinstance(message, HumanMessage):
            if message.additional_kwargs.get("lc_source") != "pinned_skill":
                continue
            marker = message.additional_kwargs.get("skill")
            if isinstance(marker, dict) and _normalized_path(marker.get("path")) == target:
                return True
    return False


def _merge_updates(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any] | None:
    if left is None:
        return right
    if right is None:
        return left
    merged = dict(left)
    for key, value in right.items():
        if key == "messages":
            merged["messages"] = [*(merged.get("messages") or []), *value]
        else:
            merged[key] = value
    return merged


class AgentSkillsMiddleware(SkillsMiddleware):
    """SkillsMiddleware restricted to the skills enabled for one agent.

    The name must stay `"SkillsMiddleware"` so deepagents replaces its own
    middleware slot in both the main agent and the general-purpose subagent.
    """

    @property
    def name(self) -> str:
        return "SkillsMiddleware"

    def __init__(
        self,
        *,
        backend: BackendProtocol,
        sources: Sequence[str | tuple[str, str]],
        enabled_skills: Iterable[str],
        system_prompt: str | None = SKILLS_SYSTEM_PROMPT,
        tools: Any = None,
    ) -> None:
        super().__init__(
            backend=backend,
            sources=sources,
            system_prompt=system_prompt,
            tools=tools,
        )
        self._enabled_skills = frozenset(enabled_skills)

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        if not request.state.get("skills_metadata"):
            return request
        return super().modify_request(request)

    def _filter(self, skills: Iterable[SkillMetadata] | None) -> list[SkillMetadata]:
        return [skill for skill in skills or [] if skill.get("name") in self._enabled_skills]

    def _filter_update(self, update: SkillsStateUpdate | None) -> SkillsStateUpdate | None:
        if update is None:
            return None
        update["skills_metadata"] = self._filter(update.get("skills_metadata"))
        return update

    def before_agent(self, state, runtime, config):  # noqa: ANN001
        update = super().before_agent(state, runtime, config)
        if update is not None:
            return self._filter_update(update)
        current = state.get("skills_metadata")
        if current is None:
            return None
        filtered = self._filter(current)
        return {"skills_metadata": filtered} if filtered != current else None

    async def abefore_agent(self, state, runtime, config):  # noqa: ANN001
        update = await super().abefore_agent(state, runtime, config)
        if update is not None:
            return self._filter_update(update)
        current = state.get("skills_metadata")
        if current is None:
            return None
        filtered = self._filter(current)
        return {"skills_metadata": filtered} if filtered != current else None

    def _skills_to_repin(self, state) -> list[SkillMetadata]:  # noqa: ANN001
        """Skills whose body/tool disclosure was lost with the summarized history."""
        messages = state.get("messages") or []
        summary_index = _last_summary_index(messages)
        if summary_index is None:
            return []
        return [
            skill
            for skill in self._filter(state.get("skills_metadata"))
            if (skill.get("metadata") or {}).get("include_tools", "").split()
            and not _skill_available_after(messages, summary_index, str(skill.get("path") or ""))
        ]

    def before_model(self, state, runtime):  # noqa: ANN001
        update = super().before_model(state, runtime)
        skills = self._skills_to_repin(state)
        if not skills:
            return update
        responses = self._backend.download_files([str(skill["path"]) for skill in skills])
        return _merge_updates(update, _pin_update(skills, responses))

    async def abefore_model(self, state, runtime):  # noqa: ANN001
        update = await super().abefore_model(state, runtime)
        skills = self._skills_to_repin(state)
        if not skills:
            return update
        responses = await self._backend.adownload_files([str(skill["path"]) for skill in skills])
        return _merge_updates(update, _pin_update(skills, responses))
