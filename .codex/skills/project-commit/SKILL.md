---
name: project-commit
description: Standard commit workflow for this repository. Use when Codex is asked to prepare, commit, or push code changes in SuperPersonalPlatform, especially after implementation work that must update project memory, run backend tests, run frontend tests, build the frontend, create a git commit, and push the current branch.
---

# Project Commit

Use this skill to finish code changes in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.

## Workflow

1. Read `AGENTS.md` and `PROJECT_MEMORY.md`.
2. Run `git status --short` before staging anything.
3. Inspect relevant diffs and separate:
   - changes made for the current task,
   - pre-existing user changes,
   - generated build output.
4. Update `PROJECT_MEMORY.md` when behavior, architecture, commands,
   dependencies, configuration, public interfaces, or operating assumptions
   changed. Update `AGENTS.md` only when the repository-level agent contract
   changed.
5. Run the required checks:
   - `.venv/bin/python -m pytest`
   - `cd web && npm test`
   - `cd web && npm run build`
6. Stage only files that belong to the current task. Use partial staging when a
   touched file also contains unrelated user edits. If the change cannot be
   separated safely, stop and explain the conflict.
7. Commit with a concise imperative message.
8. Push the current branch with `git push origin HEAD`.

## Guardrails

- Do not use `git add .` in a dirty worktree.
- Do not revert or overwrite existing changes unless the user explicitly asks.
- Do not commit `config.yaml`, virtualenvs, caches, or dependency directories.
- Treat `web/dist` as commit-worthy only when the frontend build output changed
  as part of the task or deployment path.
- Include the exact test commands and outcomes in the final response.

