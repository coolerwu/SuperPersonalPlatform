from __future__ import annotations

from typing import Any

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware


SELF_IMPROVEMENT_PROMPT = """## Self Improvement Middleware

You can maintain your own skills and improvement records when the current run reveals a reusable workflow or a durable operating lesson.

Memory is handled by MemoryMiddleware through `/memories/AGENTS.md`; this middleware only governs `/skills/` and `/improvements/`.

You may autonomously create or update your own reusable skills:
- `/skills/{skill_id}/SKILL.md`

When you create or update a skill, also record why:
- `/improvements/reviews/{run_id}.md`
- `/improvements/changes/{timestamp}_{change_id}.json`

Improvement records are audit material, not active skills. Only `/skills/{skill_id}/SKILL.md` files are active skills.
Do not store secrets in skills or improvement records.
"""


class SelfImprovementMiddleware(AgentMiddleware):
    """Inject self-maintenance rules into the DeepAgent model request."""

    def modify_request(self, request: Any) -> Any:
        system_message = append_to_system_message(request.system_message, SELF_IMPROVEMENT_PROMPT)
        return request.override(system_message=system_message)
