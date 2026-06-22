# Agent Sidebar Submenu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the four Agent modes into route-backed global sidebar submenus.

**Architecture:** AppShell owns Agent route-to-mode mapping and renders nested navigation. AgentPage remains responsible for feature state and rendering but receives its active mode from the route instead of exposing a second navigation rail.

**Tech Stack:** React 18, Vite, Vitest, Testing Library, CSS.

---

### Task 1: Define route-backed navigation behavior

**Files:**
- Modify: `web/src/main.test.jsx`
- Modify: `web/src/main.jsx`

- [x] Add a failing test that expects an `Agent 功能` sidebar navigation with four items and no `agent-mode-rail` inside the page.
- [x] Run `npm test -- src/main.test.jsx -t "renders Agent features as sidebar submenus"` and confirm it fails because the current navigation lives inside AgentPage.
- [x] Add Agent child routes in AppShell and pass the derived mode into AgentPage.
- [x] Remove the page-local mode rail and run the focused test until it passes.

### Task 2: Restyle sidebar and preserve responsive behavior

**Files:**
- Modify: `web/src/styles.css`

- [x] Add nested desktop submenu spacing and selected states.
- [x] Remove `agent-mode-rail` layout rules and let the Agent stage occupy the full content width.
- [x] Add a horizontally scrollable Agent submenu under the mobile top navigation.
- [x] Run `npm test` and `npm run build`.

### Task 3: Synchronize architecture and verify

**Files:**
- Modify: `docs/project-architecture.md`
- Modify: `design-qa.md`

- [x] Replace the local mode-rail architecture note with route-backed sidebar submenu behavior.
- [x] Verify `/agents`, `/agents/manage`, `/agents/skills`, `/agents/models`, and `/models` in Browser/IAB at desktop and mobile widths.
- [x] Run `.venv/bin/python -m pytest`, `npm test`, `npm run build`, and `git diff --check` before commit.
