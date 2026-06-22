# Agent Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove self-development and terminal capabilities end to end, then rebuild the Agent page as the selected Mission Rail command center.

**Architecture:** FastAPI stops composing the self-development and terminal routers and removes their dedicated services. React keeps the existing Agent behavior but replaces the top tabs with a vertical local mode rail and adds a contextual inspector around the existing chat/config workspaces.

**Tech Stack:** FastAPI, pytest, React 18, Vite, Vitest, Testing Library, CSS, Browser/IAB.

---

### Task 1: Lock removal behavior with tests

**Files:**
- Modify: `server/tests/test_static_routes.py`
- Modify: `web/src/main.test.jsx`

- [ ] Add a backend test that creates the full app and asserts no route path starts with `/api/self-dev` and no route equals `/api/system/terminal/connect`.
- [ ] Add a frontend test that asserts the sidebar has no “自开发” or “终端” button and direct legacy paths render the home workspace.
- [ ] Run the focused tests and verify they fail because the old routes and navigation still exist.

### Task 2: Remove backend capabilities

**Files:**
- Modify: `server/infrastructure/fastapi_app.py`
- Modify: `server/adapter/dependencies.py`
- Modify: `server/domain/agents.py`
- Modify: `server/app/agent_tool_service.py`
- Delete: `server/adapter/self_dev_routes.py`
- Delete: `server/adapter/terminal_routes.py`
- Delete: `server/app/self_dev_service.py`
- Delete: `server/app/job_service.py`
- Delete: `server/app/job_worker.py`
- Delete: `server/domain/jobs.py`
- Delete: `server/infrastructure/async_command_runner.py`
- Delete: `server/tests/test_self_dev_routes.py`
- Delete: `server/tests/test_terminal_routes.py`
- Delete: `server/tests/test_job_service.py`
- Delete: `server/tests/test_job_worker.py`
- Delete: `server/tests/test_async_command_runner.py`

- [ ] Remove router composition, worker lifecycle, container fields, self-dev tool profile, and repository tool definitions.
- [ ] Remove tests and modules owned only by the deleted capabilities.
- [ ] Run backend tests and update any Agent tool expectations to cover the remaining profiles only.

### Task 3: Remove frontend capability code

**Files:**
- Modify: `web/src/main.jsx`
- Modify: `web/src/main.test.jsx`
- Modify: `web/src/styles.css`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] Remove xterm imports, self-development/terminal components, navigation items, home entries, and legacy tests.
- [ ] Remove `@xterm/xterm` and `@xterm/addon-fit` through npm so the lockfile stays coherent.
- [ ] Run the focused frontend tests and verify the removal behavior is green.

### Task 4: Build Mission Rail Agent workspace

**Files:**
- Modify: `web/src/main.jsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/main.test.jsx`

- [ ] Add a failing test for the vertical four-mode navigation and contextual Agent runtime inspector.
- [ ] Replace the top tab bar with a local command rail while preserving existing mode state and API behavior.
- [ ] Add the chat inspector with Agent, model, skills, connection, and capability summaries derived from existing data.
- [ ] Restyle chat and config surfaces to match the selected open rail/list/panel container model.
- [ ] Add responsive mode navigation and inspector behavior for tablet/mobile.
- [ ] Run Vitest and Vite build.

### Task 5: Documentation and visual QA

**Files:**
- Modify: `config.example.yaml`
- Modify: `docs/project-architecture.md`
- Create: `design-qa.md`

- [ ] Remove the example self-dev Skill and update architecture, API, frontend, dependency, workspace, and operating notes.
- [ ] Start the app and capture the Agent page at 1440x1024 plus a mobile viewport with Browser/IAB.
- [ ] Compare the selected concept and implementation for layout, typography, palette, container model, copy, icons, and responsive behavior; fix P0-P2 differences.
- [ ] Record `final result: passed` only after functional and visual checks pass.
- [ ] Run the full backend suite, frontend suite, build, and `git status --short`.

