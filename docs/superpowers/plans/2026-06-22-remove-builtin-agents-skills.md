# Remove Built-in Agents and Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Agent and Skill workspace-owned while binding Portfolio chat to an explicitly configured ordinary Agent.

**Architecture:** Remove all Agent/Skill templates and injection paths from `AgentChatService`. Add `portfolio.agent_id` to validated workspace configuration and expose it through Agent APIs; Portfolio uses that Agent id while the Agent's own `skill_ids` resolves tools through ordinary Skill files.

**Tech Stack:** Python 3.12, FastAPI, PyYAML, React, Vitest, pytest.

---

### Task 1: Remove Backend Built-in Definitions

**Files:**
- Modify: `server/tests/test_agent_chat.py`
- Modify: `server/app/agent_chat_service.py`
- Modify: `server/adapter/agent_routes.py`

- [x] **Step 1: Write failing tests**

Add tests proving an empty `agents.definitions` and `skills.definitions` returns no injected entries, configured entries never contain `is_builtin`, and `agents.builtin_overrides` is rejected.

- [x] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: failures show `ai-investment-advisor`, `common:portfolio`, and `is_builtin` are still injected.

- [x] **Step 3: Implement minimal removal**

Delete `BUILTIN_AGENTS`, `BUILTIN_SKILLS`, `_builtin_with_overrides`, `_builtin_agent_definitions`, `_builtin_skill_definitions`, default Skill tool fallback, built-in update branches, and `is_builtin` fields/response keys. Reject `agents.builtin_overrides` while parsing configuration.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: all Agent chat tests pass.

### Task 2: Add Explicit Portfolio Agent Binding

**Files:**
- Modify: `server/infrastructure/config.py`
- Modify: `server/app/agent_chat_service.py`
- Modify: `server/adapter/agent_routes.py`
- Modify: `server/tests/test_agent_chat.py`
- Modify: `config.example.yaml`

- [x] **Step 1: Write failing config/API tests**

Add tests for `portfolio.agent_id`, including a valid ordinary Agent reference, empty binding, and rejection of an unknown Agent id. Assert `/api/agents/options` and `/api/agents/config` expose `portfolio_agent_id`.

- [x] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: responses lack `portfolio_agent_id` and invalid references are accepted.

- [x] **Step 3: Implement validated binding**

Add an infrastructure `PortfolioConfig(agent_id: str = "")`, parse `portfolio.agent_id`, validate non-empty ids against workspace Agent definitions, include the binding in options/config snapshots, and persist it from `PUT /api/agents/config`. Set `portfolio.agent_id: ""`, `skills.definitions: []`, and `agents.definitions: []` in the example template.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest server/tests/test_agent_chat.py -q`

Expected: all tests pass.

### Task 3: Remove Built-in UI and Use Portfolio Binding

**Files:**
- Modify: `web/src/main.test.jsx`
- Modify: `web/src/main.jsx`
- Modify: `web/src/styles.css`

- [x] **Step 1: Write failing frontend tests**

Assert management rows remain editable/deletable without `is_builtin`, the Portfolio page sends session/WebSocket messages with the API-provided `portfolio_agent_id`, and an empty binding renders a configuration notice without opening Agent chat.

- [x] **Step 2: Verify RED**

Run: `cd web && npm test -- --run`

Expected: hardcoded `ai-investment-advisor` calls and built-in UI assertions fail.

- [x] **Step 3: Implement minimal frontend behavior**

Remove Agent/Skill `is_builtin` badges and disabled/delete branches. Add an editable Portfolio Agent selector to Agent management, include `portfolio_agent_id` in config saves, and make `PortfolioPage` load/use the configured id for sessions and WebSocket messages. Keep holdings usable when the binding is empty or invalid.

- [x] **Step 4: Verify GREEN**

Run: `cd web && npm test -- --run && npm run build`

Expected: all frontend tests and production build pass.

### Task 4: Documentation, Migration, and Release

**Files:**
- Modify: `docs/project-architecture.md`
- Runtime-only: `.super-personal-platform/config.yaml`
- Runtime-only: `.super-personal-platform/skills/common/portfolio/SKILL.md`

- [x] **Step 1: Synchronize architecture**

Document that Agents and Skills are exclusively workspace-owned, `portfolio.agent_id` binds Portfolio to an Agent, and only that Agent's `skill_ids` selects `common:portfolio`.

- [x] **Step 2: Run required verification**

Run:

```bash
.venv/bin/python -m pytest
cd web && npm test
cd web && npm run build
```

Expected: all commands exit zero.

- [x] **Step 3: Browser QA**

Verify Agent/Skill management has no built-in badges, ordinary rows are editable, Portfolio uses the configured Agent, empty binding has a clear state, and the console has no errors.

- [ ] **Step 4: Commit and push**

Run the repository `$project-commit` workflow, stage with `git add .`, commit with an imperative message, and push `main`.

- [ ] **Step 5: Migrate production ordinary data**

Before restart, update production workspace config to contain ordinary `ai-investment-advisor`, ordinary `common:portfolio`, and `portfolio.agent_id: ai-investment-advisor`; retain the existing ordinary Skill file with four `tools.allow` names and remove `agents.builtin_overrides`.

- [ ] **Step 6: Restart and verify production**

Pull the pushed commit, restart `super-personal-platform.service`, verify the remote HEAD, `systemctl is-active`, and HTTP `200` from `/api/auth/me`.
