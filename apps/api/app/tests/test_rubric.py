import pytest

from app.services.rubric import RUBRIC_MAX_ITERATIONS, build_rubric_middleware


class _FakeRubricMiddleware:
    """Stands in for deepagents.RubricMiddleware in these unit tests."""

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.max_iterations = kwargs.get("max_iterations", 3)

    def after_agent(self, state, runtime):
        return {"delegated": "sync"}

    async def aafter_agent(self, state, runtime):
        return {"delegated": "async"}


def _build(monkeypatch):
    monkeypatch.setattr("deepagents.RubricMiddleware", _FakeRubricMiddleware)
    return build_rubric_middleware(model=object(), tools=[])


def test_rubric_middleware_keeps_grader_config(monkeypatch) -> None:
    middleware = _build(monkeypatch)

    assert isinstance(middleware, _FakeRubricMiddleware)
    assert middleware.max_iterations == RUBRIC_MAX_ITERATIONS
    assert middleware.init_kwargs["tools"] == []


@pytest.mark.asyncio
async def test_first_draft_goes_to_the_grader(monkeypatch) -> None:
    middleware = _build(monkeypatch)

    assert await middleware.aafter_agent({}, None) == {"delegated": "async"}
    assert await middleware.aafter_agent({"_rubric_iterations": 0}, None) == {
        "delegated": "async"
    }


@pytest.mark.asyncio
async def test_revision_is_not_regraded(monkeypatch) -> None:
    middleware = _build(monkeypatch)

    update = await middleware.aafter_agent({"_rubric_iterations": 1}, None)

    # A terminal status makes the next Run on this thread grade its own
    # draft instead of inheriting the exhausted counter.
    assert update == {
        "_rubric_iterations": RUBRIC_MAX_ITERATIONS,
        "_rubric_status": "max_iterations_reached",
    }


def test_revision_is_not_regraded_sync(monkeypatch) -> None:
    middleware = _build(monkeypatch)

    assert middleware.after_agent({"_rubric_iterations": 1}, None) == {
        "_rubric_iterations": RUBRIC_MAX_ITERATIONS,
        "_rubric_status": "max_iterations_reached",
    }
    assert middleware.after_agent({}, None) == {"delegated": "sync"}


def test_real_middleware_declares_the_model_jump() -> None:
    middleware = build_rubric_middleware(model=object(), tools=[])

    # The grader resumes the agent by jumping back to the model node; the
    # graph rejects that jump without this hook metadata.
    assert getattr(middleware.__class__.after_agent, "__can_jump_to__", None) == [
        "model"
    ]
    assert getattr(middleware.__class__.aafter_agent, "__can_jump_to__", None) == [
        "model"
    ]


@pytest.mark.asyncio
async def test_real_middleware_revision_stop_closes_grading() -> None:
    middleware = build_rubric_middleware(model=object(), tools=[])

    assert await middleware.aafter_agent({}, None) is None
    assert await middleware.aafter_agent({"_rubric_iterations": 1}, None) == {
        "_rubric_iterations": RUBRIC_MAX_ITERATIONS,
        "_rubric_status": "max_iterations_reached",
    }
