"""One quality gate for chat replies: grade the draft, accept one revision.

The upstream `RubricMiddleware` counts *grading passes* in `max_iterations`,
so allowing a single revision requires `max_iterations=2`. That made every
reply run the grader twice and end as `max_iterations_reached`, because the
confirmation pass re-applied the same rubric to the revision. The platform
treats a revision as final: the draft is graded once, and when it is
rejected the revised answer is streamed and persisted without another grade.
"""

from __future__ import annotations

from typing import Any

RUBRIC_MAX_ITERATIONS = 2
"""Grading passes upstream needs before it injects one revision."""


def build_rubric_middleware(
    *,
    model: Any,
    tools: list[Any],
    max_iterations: int = RUBRIC_MAX_ITERATIONS,
) -> Any:
    """Build the platform rubric middleware.

    The class is defined per call so the base stays the `deepagents`
    `RubricMiddleware` the caller imported (tests replace it, and importing
    `deepagents` eagerly would slow module import).
    """
    from deepagents import RubricMiddleware
    from langchain.agents.middleware.types import hook_config

    class PlatformRubricMiddleware(RubricMiddleware):
        """Grade the draft once; a rejected draft is revised and accepted.

        `_rubric_iterations` is the upstream private counter of completed
        grades for the current rubric attempt. The first natural stop is the
        draft (0); any later stop is the revision, which is final for us.
        Closing the grading run with a terminal status makes the next Run on
        the same thread reset the counter and grade its own draft.
        """

        def _revision_is_final(self, state: dict[str, Any]) -> bool:
            return int(state.get("_rubric_iterations") or 0) >= 1

        def _close_grading_run(self) -> dict[str, Any]:
            return {
                "_rubric_iterations": self.max_iterations,
                "_rubric_status": "max_iterations_reached",
            }

        # The grader asks the agent to revise by jumping back to the model
        # node; the graph only allows that when the hook declares it.
        @hook_config(can_jump_to=["model"])
        def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            if self._revision_is_final(state):
                return self._close_grading_run()
            return super().after_agent(state, runtime)

        @hook_config(can_jump_to=["model"])
        async def aafter_agent(
            self, state: Any, runtime: Any
        ) -> dict[str, Any] | None:
            if self._revision_is_final(state):
                return self._close_grading_run()
            return await super().aafter_agent(state, runtime)

    return PlatformRubricMiddleware(
        model=model,
        tools=tools,
        max_iterations=max_iterations,
    )
