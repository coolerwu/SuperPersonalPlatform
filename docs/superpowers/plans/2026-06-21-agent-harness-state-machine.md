# Agent Harness State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace TOOLS mode with an injected AGENT runner implementing strict goal, evidence, verification, and finalization phases.

**Architecture:** A runtime registry owns PromptRunner and AgentRunner instances. Agent is pure configuration data. AgentRunner owns a run-scoped evidence ledger and uses fresh model calls for goal creation, semantic verification, and final formatting.

**Tech Stack:** Python 3.12, dataclasses, async protocols, pytest

---

### Task 1: Define the new public contract

**Files:** `server/tests/test_agent_harness.py`, `server/domain/harness/contracts.py`

- [ ] Replace TOOLS expectations with AGENT and assert the full public phase list.
- [ ] Assert Agent contains no model gateway field and run_agent receives an injected runtime.
- [ ] Run `.venv/bin/pytest server/tests/test_agent_harness.py -q` and verify RED.

### Task 2: Implement classified runners

**Files:** `server/domain/harness/runner.py`, `server/domain/harness/modes/prompt.py`, `server/domain/harness/modes/agent.py`, `server/domain/harness/__init__.py`

- [ ] Implement PromptRunner with one completion call.
- [ ] Implement AgentRunner phases, structured goal/verification parsing, sequential actions, evidence observation, strict verification, and finalization.
- [ ] Delete the replaced root `agent.py`, `prompt.py`, and `tools.py` modules.
- [ ] Run focused Harness tests and verify GREEN.

### Task 3: Migrate application wiring

**Files:** `server/app/agent_chat_service.py`, `server/tests/test_agent_chat.py`, `docs/project-architecture.md`

- [ ] Build the runtime once in AgentChatService and select AGENT only when effective tools exist.
- [ ] Update callers and architecture documentation from TOOLS to AGENT.
- [ ] Run focused caller tests and then all project checks.
