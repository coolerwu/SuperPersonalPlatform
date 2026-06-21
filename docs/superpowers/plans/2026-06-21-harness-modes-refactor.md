# Harness Modes Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Agent Harness into explicit prompt and tool modes with one deterministic async state machine and a narrow public package API.

**Architecture:** `HarnessRequest` carries an explicit `HarnessMode`, while `AgentRunState` and `AgentRunPhase` model tool-loop transitions. `run_agent` dispatches prompt mode directly and tool mode through a single Python loop; LangGraph and the duplicate fallback are removed.

**Tech Stack:** Python 3.12, dataclasses, enum, typing Protocol, pytest

---

### Task 1: Lock the multi-mode public contract

**Files:**
- Create: `server/tests/test_agent_harness.py`
- Modify: `server/domain/harness/agent.py`
- Modify: `server/domain/harness/__init__.py`

- [ ] **Step 1: Write failing tests for explicit modes**

Add tests importing `HarnessMode`, `HarnessRequest`, `AgentRunPhase`, and `run_agent`. Verify prompt mode calls `complete`, tool mode performs `reason_with_tools -> dispatch -> reason_with_tools`, and invalid mode-specific configuration raises `ValueError`.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py -q`

Expected: collection fails because the new public types do not exist.

- [ ] **Step 3: Add the public types and package exports**

Implement:

```python
class HarnessMode(StrEnum):
    PROMPT = "prompt"
    TOOLS = "tools"

class AgentRunPhase(StrEnum):
    REASONING = "reasoning"
    TOOL_RUNNING = "tool_running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"

@dataclass(frozen=True)
class HarnessRequest:
    mode: HarnessMode
    content: str
    images: tuple[ChatImage, ...] = ()
    tool_names: tuple[str, ...] = ()
    tool_registry: AgentToolDispatcher | None = None
    tool_runtime: object | None = None
```

Export only the stable caller-facing types from `server/domain/harness/__init__.py`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py -q`

Expected: all focused Harness tests pass.

### Task 2: Replace the graph/fallback pair with one state machine

**Files:**
- Modify: `server/tests/test_agent_harness.py`
- Modify: `server/domain/harness/agent.py`

- [ ] **Step 1: Write failing transition and limit tests**

Add assertions for checkpoint order, sequential tool dispatch, empty tool reasoning retries, `max_iterations` validation, and exactly one forced final response at the limit.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py -q`

Expected: failures identify missing phase transitions or validation.

- [ ] **Step 3: Implement the explicit loop**

Remove `AgentGraphState`, the optional `langgraph` import, graph compilation, and the manual fallback. Implement immutable state replacement through phases:

```python
REASONING -> TOOL_RUNNING -> REASONING
REASONING -> COMPLETED
REASONING -> FINALIZING -> COMPLETED
```

Increment `turn` once per reasoning attempt, dispatch tool calls sequentially, and call `force_tool_final` once after the configured limit.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py -q`

Expected: all focused Harness tests pass.

### Task 3: Migrate callers and remove LangGraph dependency

**Files:**
- Modify: `server/app/agent_chat_service.py`
- Modify: `server/infrastructure/llm_client.py`
- Modify: `server/tests/test_agent_chat.py`
- Modify: `server/tests/test_llm_client.py`
- Modify: `pyproject.toml`
- Modify: `docs/project-architecture.md`

- [ ] **Step 1: Write or update failing caller tests**

Update callers to construct `HarnessRequest(mode=HarnessMode.PROMPT, ...)` or `HarnessRequest(mode=HarnessMode.TOOLS, ...)`. Update direct low-level imports to use `server.domain.harness.agent` instead of the package root.

- [ ] **Step 2: Run affected tests and verify RED**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py server/tests/test_agent_chat.py server/tests/test_llm_client.py -q`

Expected: failures identify old context types and exports.

- [ ] **Step 3: Migrate production callers**

Replace `PromptSkillContext` and `ReactSkillContext` construction with explicit requests, remove obsolete exports, remove `langgraph>=0.2`, and document explicit Harness modes and state behavior in the architecture guide.

- [ ] **Step 4: Run affected and full verification**

Run: `.venv/bin/pytest server/tests/test_agent_harness.py server/tests/test_agent_chat.py server/tests/test_llm_client.py -q`

Run: `.venv/bin/pytest -q`

Expected: all tests pass.
