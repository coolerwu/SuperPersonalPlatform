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
   - Before SSH, record the pushed target SHA with `git rev-parse HEAD`.
   - On the remote host, run from `SuperPersonalPlatform/` and make each phase
     observable: pull, HEAD check, restart, active/status check.
   - Pull with `git -c http.version=HTTP/1.1 pull`; retry transient network/TLS
     failures up to 3 times with a short sleep before declaring pull failed.
   - After pull, verify remote `git rev-parse HEAD` exactly equals the local
     target SHA recorded before SSH. Do not restart or claim deployment success
     if the remote HEAD is different.
   - After the HEAD check and before restart, synchronize production runtime
     dependencies because the systemd unit starts `.venv/bin/python -m server`
     directly and does not run `run.sh` dependency installation on restart:
     run `.venv/bin/python -m pip install .`.
   - Ensure Playwright browser binaries are present before restart when browser
     tooling is in the project. Prefer
     `PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright} .venv/bin/python -m playwright install chromium`
     on the production host so CDN issues do not leave `browser_extract` without
     Chromium.
   - Restart with `sudo -n systemctl restart super-personal-platform.service`.
     Check service health with non-sudo
     `systemctl is-active super-personal-platform.service` and
     `systemctl status super-personal-platform.service --no-pager --lines=12`.
     If a sudo status check fails after restart, rerun the non-sudo checks
     before treating the deployment as failed.
   - Do not store the SSH/sudo password in repository files. Use an existing
     authenticated SSH session, ask the user for the password, or read it from a
     local uncommitted environment variable such as
     `SUPER_PERSONAL_PROD_SSH_PASSWORD`.
   - Prefer a non-interactive `expect` wrapper when a password prompt is
     expected, but never embed a literal password in the command string or saved
     shell history. Read it from process environment or prompt at runtime. If an
     `expect`/`ssh` attempt hangs at a password prompt, terminate the stale local
     process before retrying.
   - The final response must separately report push outcome, remote pull/HEAD
     outcome, dependency/browser install outcome, restart outcome, and service
     active/status outcome. If any phase fails, report that phase and do not
     claim deployment completed.

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
