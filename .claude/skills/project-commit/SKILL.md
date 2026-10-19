---
name: project-commit
description: Commit workflow for the v1 Agent Command Center (FastAPI + deepagents in apps/api, React/Vite in apps/web). Use when asked to prepare, commit, or push changes. Keeps docs/intent.md, CLAUDE.md, README, deploy config, and the API/web checks in sync.
---

# Project Commit

Use this skill to finish changes in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.

## Workflow

1. Read `CLAUDE.md` and `docs/intent.md`.
2. Run `git status --short` before staging anything.
3. Inspect the diff and confirm only the intended files are in scope.
4. Synchronize documentation when behavior, interfaces, dependencies, commands,
   or deployment changed:
   - `docs/intent.md` for requirements/acceptance changes;
   - `CLAUDE.md` for agent workflow or long-term collaboration changes;
   - `README.md` for local/dev/deploy commands and configuration;
   - `deploy/.env.example` for new required environment variables;
   - this skill when the commit workflow itself changes.
5. Run the required checks:
   - `.venv/bin/python -m pytest apps/api/app/tests`
   - `cd apps/web && npm run build` (run `npm install` first when
     `package.json` changed)
   - `apps/web/dist` is committed; include the rebuilt assets in the commit.
   Production does not run npm; FastAPI serves the committed dist.
6. Stage all uncommitted code with the exact command `git add .`.
7. Commit with a concise imperative message.
8. Push the current branch with `git push origin HEAD`.

## Remotes and rc-clone

- The only configured remote is `origin` → `git@github.com:coolerwu/SuperPersonalPlatform.git`.
- Push only to `origin`; do not add/remove remotes or rewrite history.
- A local `rc-clone` is a separate checkout. Pushing to GitHub does not modify it,
  so there is no write conflict. If it needs the new commit, pull it explicitly
  inside that checkout; never run `git pull` or `git reset` in `rc-clone` from
  this repository.
- If deploying from `rc-clone`, pull it first, verify its HEAD equals the pushed
  SHA, then run the deployment commands there.

## Deployment (v1)

Production runs as a **user-level systemd service** on the host:
`qiuqiu@192.168.1.3:~/SuperPersonalPlatform-v1-deploy`, Python venv, SQLite,
port 8888. FastAPI serves `apps/web/dist` directly. Docker Compose, nginx, and
Postgres are no longer used; their deployment files were removed.

If the user explicitly asks to deploy v1 on the target machine:

1. Push the target branch to `origin` first and record the pushed SHA.
2. On the target host:
   `cd ~/SuperPersonalPlatform-v1-deploy && git pull --ff-only origin main`
   Verify `git rev-parse HEAD` equals the pushed SHA and `git status --short` is clean.
3. If `apps/api/pyproject.toml` changed, run `.venv/bin/pip install -e apps/api`.
   If Playwright was added or upgraded, also run
   `.venv/bin/playwright install chromium` (prefix
   `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright` when the
   default download is slow). If Chromium fails to start for missing shared
   libraries, run `sudo .venv/bin/playwright install-deps chromium`.
4. If `deploy/agent-command-center.service` changed, copy it to
   `~/.config/systemd/user/` and run `systemctl --user daemon-reload`.
5. Restart the service:
   `systemctl --user restart agent-command-center.service`
6. Verify separately with:
   `systemctl --user is-active agent-command-center.service`
   `curl --noproxy '*' http://127.0.0.1:8888/api/health`
   Authenticate with `APP_AUTH_TOKEN` from `~/.super-personal-platform/.env`
   without printing it.
7. Report pull/HEAD, dependency install, service state, and health separately.
   Do not claim deployment success if any phase failed.
8. Never put SSH/sudo passwords, API keys, or tokens in repository files or
   command text.
9. Do not delete `~/.super-personal-platform/`. Back up
   `~/.super-personal-platform/data/app.db` and `data/checkpoints/` before
   schema changes.
10. Docker deployment files have been removed; do not reintroduce Docker
    Compose, nginx, or Postgres.

## Guardrails

- This project intentionally commits all uncommitted code during this workflow.
- Always use `git add .`; do not stage files one by one.
- Do not commit `.env`, `data/`, `.super-personal-platform/`, `node_modules/`,
  virtualenvs, caches, or dependency directories.
- `apps/web/dist/` **is** committed; include the rebuilt assets.
- Do not reset or overwrite existing changes unless the user explicitly asks.
- Do not assume a push synchronized `rc-clone`; it is an independent checkout.
- v1 has workspace filesystem tools, long-term memory, and a skills library
  (per-agent enabled skills; skill-provided tools unlock only after the model
  reads the skill's `SKILL.md`). Browser tools (`browser_*`) are skill-gated via
  `web-browser` and use a local Playwright Chromium; there are no shell tools and
  no approval flow; do not describe those as available.
- Production uses systemd + venv + SQLite. Docker deployment is deprecated:
  do not reintroduce Docker Compose, Dockerfiles, nginx, or Postgres.
- If a commit workflow change is requested, update both `CLAUDE.md` and this
  skill before continuing.
- Include the exact test/build commands and their outcomes in the final response.
