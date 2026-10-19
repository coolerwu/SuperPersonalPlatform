from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.skills import (
    SKILLS_SYSTEM_PROMPT,
    SkillMetadata,
    SkillsMiddleware,
    SkillsStateUpdate,
)
from langchain.agents.middleware.types import ModelRequest


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
