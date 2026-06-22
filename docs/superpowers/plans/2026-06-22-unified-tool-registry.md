# Unified Tool Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy tool model with a unified registry, make Agent Skill binding selectable, and isolate chat sessions by Agent.

**Architecture:** Backend tool definitions become the only metadata and dispatch source. Skills store only allowed tool names, the frontend loads registry metadata through an API, and session APIs enforce Agent ownership at every read and write boundary.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React 18, Vitest, Vite.

---

### Task 1: Define the unified Tool Registry

**Files:**
- Modify: `server/app/agent_tool_service.py`
- Modify: `server/domain/agents.py`
- Test: `server/tests/test_agent_harness.py`

- [ ] **Step 1: Write failing registry tests**

Add tests that construct a registry tool with `name`, `display_name`, `description`, `input`, `output`, and `support_scene`, assert public metadata and generated model schema, and reject duplicate names or unsupported scenes.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_harness.py -q`

Expected: failures because the current tool definition has no public output schema or scene metadata.

- [ ] **Step 3: Implement the registry model**

Replace static support/profile constants with registry-owned validation. Keep handlers private and expose a stable `public_definitions()` result sorted by `name`. Derive the LLM function schema from `input` and validate structured handler results against `output` where applicable.

- [ ] **Step 4: Verify the registry tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_harness.py -q`

Expected: all registry and existing harness tests pass.

### Task 2: Remove the legacy tool configuration paths

**Files:**
- Modify: `server/domain/agents.py`
- Modify: `server/infrastructure/config.py`
- Modify: `server/app/agent_chat_service.py`
- Modify: `server/app/agent_skill_service.py`
- Modify: `server/adapter/agent_routes.py`
- Test: `server/tests/test_agent_chat.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests proving Skill frontmatter accepts only `tools.allow`, Agent runtime resolves only bound Skill tools, and old `profile`, `deny`, `common_skill_tools`, and platform `tools` fields are rejected.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: failures because legacy fields are still parsed and merged.

- [ ] **Step 3: Implement the direct replacement**

Simplify `ToolAccessDefinition` to an allow-list, remove profile/deny/common/platform merging, validate allowed names through the registry, and remove legacy fields from config snapshots and update payloads.

- [ ] **Step 4: Add the tools API**

Add authenticated `GET /api/agents/tools` returning `{ "tools": registry.public_definitions() }`.

- [ ] **Step 5: Verify backend configuration tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py server/tests/test_agent_harness.py -q`

Expected: both files pass with no legacy fallback behavior.

### Task 3: Enforce Agent-owned sessions

**Files:**
- Modify: `server/app/chat_session_service.py`
- Modify: `server/adapter/session_routes.py`
- Modify: `server/adapter/agent_routes.py`
- Test: `server/tests/test_agent_chat.py`

- [ ] **Step 1: Write failing isolation tests**

Add tests that create sessions for two Agents, require `agent_id` on list/detail/update/delete, return only matching summaries, and reject a WebSocket message whose session belongs to another Agent without appending messages.

- [ ] **Step 2: Verify the tests fail**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: failures because session listing is global and chat persistence does not validate ownership.

- [ ] **Step 3: Implement ownership checks**

Filter session summaries by Agent, add a shared ownership guard for single-session operations, and validate session ownership before starting Agent execution or saving messages.

- [ ] **Step 4: Verify isolation tests pass**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: all Agent chat and session tests pass.

### Task 4: Replace frontend Skill and tool inputs

**Files:**
- Modify: `web/src/main.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/main.test.jsx`

- [ ] **Step 1: Write failing UI tests**

Add tests asserting Agent management renders a searchable multi-select rather than a Skill IDs textarea, allows zero selections, loads `/api/agents/tools`, filters tools by scene, and writes selected tool names to `tools.allow`.

- [ ] **Step 2: Verify the tests fail**

Run: `cd web && npm test -- src/main.test.jsx`

Expected: failures because the current UI has a textarea and hardcoded tool constants.

- [ ] **Step 3: Implement Skill selection**

Render available Skills as selectable rows/chips inside expanded Agent details. Preserve `skill_ids: []` in saved Agent payloads and show invalid references until removed.

- [ ] **Step 4: Implement registry-driven tools UI**

Remove `AGENT_TOOL_OPTIONS` and `AGENT_TOOL_GROUPS`, load tools from the API, add `all/mcp/dag/agent` scene filters, search `name/display_name/description`, and add collapsible input/output schema details.

- [ ] **Step 5: Verify frontend capability tests pass**

Run: `cd web && npm test -- src/main.test.jsx`

Expected: updated Agent and Skill management tests pass.

### Task 5: Isolate frontend conversation state

**Files:**
- Modify: `web/src/main.jsx`
- Test: `web/src/main.test.jsx`

- [ ] **Step 1: Write failing Agent-switch tests**

Add tests asserting each Agent requests its own session list, switching clears the previous messages, restores only that Agent's remembered session, ignores stale responses, and disables Agent/session switches while sending.

- [ ] **Step 2: Verify the tests fail**

Run: `cd web && npm test -- src/main.test.jsx`

Expected: failures because the current page keeps one global session list and message array.

- [ ] **Step 3: Implement per-Agent state**

Request `/api/sessions?agent_id=...`, track `activeSessionIdByAgent`, reset the visible conversation before each switch, ignore stale loads through a request counter, and never change Agent from a selected session.

- [ ] **Step 4: Verify Agent-switch tests pass**

Run: `cd web && npm test -- src/main.test.jsx`

Expected: all frontend tests pass.

### Task 6: Synchronize documentation and verify

**Files:**
- Modify: `docs/project-architecture.md`
- Modify: `config.example.yaml`
- Modify: `docs/superpowers/plans/2026-06-22-unified-tool-registry.md`

- [ ] **Step 1: Update architecture and configuration docs**

Document the registry fields/API, Skill allow-only format, removed legacy fields, selectable Agent Skills, and Agent-owned session contract. Update the example config to the new format.

- [ ] **Step 2: Run full verification**

Run:

```bash
.venv/bin/python -m pytest
cd web && npm test
cd web && npm run build
```

Expected: all commands exit zero.

- [ ] **Step 3: Run the repository commit workflow**

Use `.codex/skills/project-commit/SKILL.md`: inspect the final diff, run `git add .`, commit all changes, push `origin HEAD`, pull on production, and restart `super-personal-platform.service`.
