from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


SKILL_IMPROVEMENT_PROMPT = """## Skill Improvement Middleware

You may maintain your own reusable skills when the current run reveals a repeatable workflow, tool usage pattern, or durable operating lesson.

This middleware is only for skill maintenance:
- active skills live in `/skills/{skill_id}/SKILL.md`
- improvement records live in `/improvements/`

Memory is handled separately by MemoryMiddleware through `/memories/AGENTS.md`.

When you create or update a skill, also record why:
- `/improvements/reviews/{run_id}.md`
- `/improvements/changes/{timestamp}_{change_id}.json`

Improvement records are audit material, not active skills. Only `/skills/{skill_id}/SKILL.md` files are active skills.
Do not store secrets in skills or improvement records.

Existing skills are authoritative user workflows. Preserve their triggers, required steps, output contract,
tool and subagent usage, execution topology, serial/parallel ordering, approval boundaries, and other explicit
MUST/NEVER constraints. Do not replace a required subagent chain with main-agent roleplay, merge serial steps
into one model response, or change serial work into parallel work unless the user explicitly requests that exact
contract change in the current conversation.

Text between `<!-- BEGIN USER CONTRACT -->` and `<!-- END USER CONTRACT -->` is immutable to Agent file tools.
You may improve text around that block. If evidence suggests the protected contract should change, write a
proposal and evidence under `/improvements/`; do not modify or remove the protected block. Treat old reviews,
reflections, and change records as historical claims, and verify platform behavior against the current runtime
before relying on them.
"""


class SkillImprovementMiddleware(AgentMiddleware):
    """Inject skill maintenance rules into the DeepAgent model request."""

    def modify_request(self, request: ModelRequest) -> ModelRequest:
        system_message = append_to_system_message(request.system_message, SKILL_IMPROVEMENT_PROMPT)
        return request.override(system_message=system_message)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self.modify_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self.modify_request(request))
