---
name: project-commit
description: Standard commit workflow for this repository. Use when Codex is asked to prepare, commit, or push code changes in SuperPersonalPlatform, especially after implementation work that must sync docs/project-architecture.md, AGENTS.md, and affected skill or operating files before tests, commit, and push.
---

# Project Commit

Use this skill to finish code changes in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.

## Workflow

1. Read `AGENTS.md` and `docs/project-architecture.md`.
2. Run `git status --short` before staging anything.
3. Inspect the current diff so the final summary can describe what is being committed.
4. Before committing, synchronize documentation and operating instructions for
   the current change:
   - Update `docs/project-architecture.md` when behavior, architecture,
     commands, dependencies, configuration, public interfaces, or operating
     assumptions changed.
   - Update `AGENTS.md` when agent workflow, commit policy, skill usage,
     repository-level collaboration rules, or long-term operating expectations
     changed.
   - Update any affected `.codex/skills/*/SKILL.md`, README, config template,
     or other corresponding file when the change modifies that workflow,
     command, interface, or documented behavior.
   - Do not proceed to staging until these matching files reflect the actual
     code and process changes being committed.
5. Run the required checks:
   - `.venv/bin/python -m pytest`
   - `cd web && npm test`
   - `cd web && npm run build`
6. Stage all uncommitted code by running the exact command `git add .`.
7. Commit with a concise imperative message.
8. Push the current branch with `git push origin HEAD`.
9. After a successful push, restart production on `qiuqiu@192.168.1.3`:
   - SSH target: `qiuqiu@192.168.1.3`
   - Remote repo: `SuperPersonalPlatform/`
   - Remote command: `cd SuperPersonalPlatform/ && git pull && sudo systemctl restart super-personal-platform.service`
   - Do not store the SSH/sudo password in repository files. Use an existing
     authenticated SSH session, ask the user for the password, or read it from a
     local uncommitted environment variable such as
     `SUPER_PERSONAL_PROD_SSH_PASSWORD`.
   - Prefer a non-interactive `expect` wrapper when a password prompt is
     expected, and include a short production restart status in the final
     response. If the restart fails, report the failure and do not claim the
     deployment completed.

## Guardrails

- This project intentionally commits all uncommitted code during this workflow.
- Always use `git add .` for staging in this workflow; do not stage files one by
  one or use partial staging.
- Do not revert or overwrite existing changes unless the user explicitly asks.
- Do not commit `config.yaml`, virtualenvs, caches, or dependency directories.
- Treat `web/dist` as commit-worthy only when the frontend build output changed
  as part of the task or deployment path.
- If a user asks to adjust this commit workflow, update both `AGENTS.md` and
  this skill before continuing the commit.
- Include the exact test commands, push outcome, and production restart outcome
  in the final response.
