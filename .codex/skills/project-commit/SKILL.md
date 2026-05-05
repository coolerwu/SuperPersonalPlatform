---
name: project-commit
description: Standard commit workflow for this repository. Use when Codex is asked to prepare, commit, or push code changes in SuperPersonalPlatform, especially after implementation work that must update docs/project-architecture.md, run backend tests, run frontend tests, build the frontend, create a git commit, and push the current branch.
---

# Project Commit

Use this skill to finish code changes in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.

## Workflow

1. Read `AGENTS.md` and `docs/project-architecture.md`.
2. Run `git status --short` before staging anything.
3. Inspect the current diff so the final summary can describe what is being committed.
4. Update `docs/project-architecture.md` when behavior, architecture, commands,
   dependencies, configuration, public interfaces, or operating assumptions
   changed. Update `AGENTS.md` only when the repository-level agent contract
   changed.
5. Run the required checks:
   - `.venv/bin/python -m pytest`
   - `cd web && npm test`
   - `cd web && npm run build`
6. Stage all uncommitted code by running the exact command `git add .`.
7. Commit with a concise imperative message.
8. Push the current branch with `git push origin HEAD`.

## Guardrails

- This project intentionally commits all uncommitted code during this workflow.
- Always use `git add .` for staging in this workflow; do not stage files one by
  one or use partial staging.
- Do not revert or overwrite existing changes unless the user explicitly asks.
- Do not commit `config.yaml`, virtualenvs, caches, or dependency directories.
- Treat `web/dist` as commit-worthy only when the frontend build output changed
  as part of the task or deployment path.
- Include the exact test commands and outcomes in the final response.
