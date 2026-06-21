# Model-driven Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ModelDefinition.mode` select prompt or agent execution without a runtime or model-client argument.

**Architecture:** Configuration owns the execution-mode choice. Stateless `run_agent` creates a model-bound `ModelRunner`, then creates the selected mode runner; requests carry only input and optional tool context. `AgentChatService` resolves tools only for agent-mode models.

**Tech Stack:** Python 3.12, dataclasses, FastAPI/Pydantic, pytest, React, Vitest.

---

### Task 1: Model Mode Configuration

**Files:**
- Modify: `server/domain/agents.py`
- Modify: `server/infrastructure/config.py`
- Modify: `server/adapter/agent_routes.py`
- Modify: `server/app/agent_chat_service.py`
- Modify: `config.example.yaml`
- Test: `server/tests/test_agent_chat.py`

- [ ] **Step 1: Write failing configuration tests**

Add assertions that omitted mode becomes `prompt`, `mode: agent` is preserved by configuration parsing and API payloads, and unsupported values raise `AgentConfigError`.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`
Expected: failures because `ModelDefinition` and response DTOs do not expose `mode`.

- [ ] **Step 3: Implement the model mode**

Define the shared enum and field:

```python
class HarnessMode(StrEnum):
    PROMPT = "prompt"
    AGENT = "agent"

@dataclass(frozen=True)
class ModelDefinition:
    ...
    mode: HarnessMode = HarnessMode.PROMPT
```

Parse with `HarnessMode(str(raw.get("mode") or "prompt"))`, serialize using
`model.mode.value`, and add the field to API DTOs and `config.example.yaml`.

- [ ] **Step 4: Verify configuration tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`
Expected: all tests pass.

### Task 2: Stateless Harness Dispatch

**Files:**
- Modify: `server/domain/harness/contracts.py`
- Modify: `server/domain/harness/runner.py`
- Modify: `server/domain/harness/__init__.py`
- Test: `server/tests/test_agent_harness.py`

- [ ] **Step 1: Write failing Harness tests**

Construct prompt and agent `ModelDefinition` values, construct mode-free
`HarnessRequest`, and assert `run_agent(...)` selects the matching runner. Cover
agent mode without tool definitions.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_harness.py -q`
Expected: failures because requests still require mode and stateless dispatch does not exist.

- [ ] **Step 3: Implement the Harness API**

Remove `HarnessRequest.mode` and runtime containers. Implement:

```python
async def run_agent(agent, request, options=None):
    model_runner = create_model_runner(agent.model)
    if agent.model.mode is HarnessMode.PROMPT:
        runner = PromptRunner(model_runner)
    else:
        runner = AgentRunner(model_runner, LLMVerifier(model_runner))
    return await runner.run(agent, request, options)
```

Export `run_agent`; remove `create_harness_runtime`, `HarnessRuntime`,
`AgentHarness`, and model-client injection.

- [ ] **Step 4: Verify Harness tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_harness.py -q`
Expected: all tests pass.

### Task 3: Application Mode Resolution

**Files:**
- Modify: `server/app/agent_chat_service.py`
- Test: `server/tests/test_agent_chat.py`

- [ ] **Step 1: Write failing application tests**

Verify an agent-mode model enters the strict loop without tools, a prompt-mode
model completes directly even when skills expose tools, and service-created
requests never specify a mode.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`
Expected: mode is still inferred from tool availability.

- [ ] **Step 3: Implement model-driven request construction**

Remove the model-client constructor parameter, resolve the model before building
tool context, attach registry/runtime only for `HarnessMode.AGENT`, and call
`run_agent(...)`.

- [ ] **Step 4: Verify application tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`
Expected: all tests pass.

### Task 4: Model Editor

**Files:**
- Modify: `web/src/main.jsx`
- Modify: `web/src/main.test.jsx`

- [ ] **Step 1: Write a failing UI test**

Open model configuration, select `Agent` mode, save, and assert the PUT body
contains `mode: "agent"`.

- [ ] **Step 2: Verify the UI test fails**

Run: `npm test -- --run` from `web/`.
Expected: no mode selector or saved mode exists.

- [ ] **Step 3: Implement the mode selector**

Default new/legacy rows to `prompt`, normalize mode during save, display a
Prompt/Agent selector and a mode badge in each model row.

- [ ] **Step 4: Verify frontend tests pass**

Run: `npm test -- --run` from `web/`.
Expected: all tests pass.

### Task 5: Documentation And Full Verification

**Files:**
- Modify: `docs/project-architecture.md`

- [ ] **Step 1: Update architecture memory**

Document model-owned mode selection, mode-free requests, stateless dispatch,
legacy default behavior, and the model editor field.

- [ ] **Step 2: Run full verification**

Run `.venv/bin/python -m pytest`, `npm test`, and `npm run build`.
Expected: 100% pass; Vite may retain its existing chunk-size warning.

- [ ] **Step 3: Execute project commit workflow**

Use `.codex/skills/project-commit/SKILL.md`: inspect status/diff, `git add .`,
commit all changes, push `origin HEAD`, pull on production, restart
`super-personal-platform.service`, and verify it is active.
