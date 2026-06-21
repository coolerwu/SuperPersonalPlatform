# Harness Mode Modules Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate Harness contracts, prompt execution, tool execution, and mode routing into focused modules without changing caller behavior.

**Architecture:** `contracts.py` owns shared DTOs and protocols, `prompt.py` owns prompt-mode validation and execution, `tools.py` owns tool-mode state and execution, and `agent.py` becomes the mode dispatcher. The package root remains the stable caller-facing API.

**Tech Stack:** Python 3.12, dataclasses, enum, typing Protocol, pytest

---

### Task 1: Lock the module boundaries

**Files:**
- Modify: `server/tests/test_agent_harness.py`

- [ ] Add a test importing `run_prompt_mode` from `server.domain.harness.prompt` and `run_tools_mode` plus `AgentRunPhase` from `server.domain.harness.tools`.
- [ ] Run `.venv/bin/pytest server/tests/test_agent_harness.py -q` and verify collection fails because the modules do not exist.

### Task 2: Split the implementation

**Files:**
- Create: `server/domain/harness/contracts.py`
- Create: `server/domain/harness/prompt.py`
- Create: `server/domain/harness/tools.py`
- Modify: `server/domain/harness/agent.py`
- Modify: `server/domain/harness/__init__.py`

- [ ] Move shared errors, DTOs, protocols, `Agent`, `HarnessMode`, `HarnessRequest`, and `ChatOptions` to `contracts.py`.
- [ ] Implement prompt-mode validation and completion in `prompt.py`.
- [ ] Move `AgentRunPhase`, `AgentRunState`, sequential tool dispatch, and forced finalization to `tools.py`.
- [ ] Reduce `agent.py` to common option validation, checkpoint emission, and a mode-runner dispatch table.
- [ ] Keep `server.domain.harness` exports compatible with existing callers.
- [ ] Run `.venv/bin/pytest server/tests/test_agent_harness.py server/tests/test_agent_chat.py server/tests/test_llm_client.py -q` and verify all focused tests pass.

### Task 3: Document and verify

**Files:**
- Modify: `docs/project-architecture.md`

- [ ] Document the file-level mode boundaries.
- [ ] Run `.venv/bin/python -m pytest`, `cd web && npm test`, and `cd web && npm run build`.
- [ ] Run `git diff --check` and verify no formatting errors.
