---
name: project-commit
description: Commit workflow for the v1 Agent Command Center (FastAPI + Claude Agent SDK in apps/api, React/Vite in apps/web). Use when asked to prepare, commit, or push changes. Keeps docs/intent.md, CLAUDE.md, README, deploy config, and the API/web checks in sync.
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

## Remotes

- The only configured remote is `origin` → `git@github.com:coolerwu/SuperPersonalPlatform.git`.
- Push only to `origin`; do not add/remove remotes or rewrite history.

## Deployment (v1)

Production runs as a **user-level systemd service** on the host:
`qiuqiu@192.168.1.3:~/SuperPersonalPlatform-v1-deploy`, Python venv, SQLite,
port 8888. FastAPI serves `apps/web/dist` directly. Docker Compose, nginx, and
Postgres are no longer used; their deployment files were removed.

Runtime state lives in `~/.super-personal-platform/`:

- `data/settings.json` — platform config, including providers, agents, channels,
  knowledge; written atomically with a `.bak` backup.
- `data/app.db` — SQLite. New installs create only `audit_log`, `cron_jobs`,
  `channel_inbounds`, `channel_deliveries`, `backtests`, `running_sessions`;
  upgraded databases keep unused legacy tables.
- `claude/` — SDK config, user profile `CLAUDE.md`, session transcripts and
  auto memory.
- `workspaces/`, `skills/`, `knowledge/`, `tmp/`, and the JSON/JSONL data files
  listed in `README.md`.

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
9. Do not delete `~/.super-personal-platform/`. Before schema changes or risky
   upgrades, back up `data/app.db`, `data/settings.json`, `claude/`, and
   `workspaces/`.
10. Docker deployment files have been removed; do not reintroduce Docker
    Compose, nginx, or Postgres.

### Upgrading from the deepagents/LangGraph version

- Back up `data/app.db` and `data/settings.json` first.
- No data migration is performed: old Provider/Agent config, knowledge config,
  memories and chat history are not imported. `settings.json` is seeded from
  `.env` and defaults (only `app_configs.session_retention_days` is read once).
- Old `channel_deliveries` / `channel_inbounds` tables are rebuilt when their
  columns are stale, so pending deliveries and inbound dedup rows are lost.
- WeChat channels must be re-scanned because their credentials lived in the old
  `channels` table, not in `settings.json`.
- Old legacy tables stay in `app.db` but are no longer read or written.
- Rebuilding `app.db` loses backtest history, cron jobs, audit rows, pending
  deliveries and inbound dedup records, but not `settings.json` or transcripts.

## Guardrails

- This project intentionally commits all uncommitted code during this workflow.
- Always use `git add .`; do not stage files one by one.
- Do not commit `.env`, `data/`, `.super-personal-platform/`, `node_modules/`,
  virtualenvs, caches, or dependency directories.
- `apps/web/dist/` **is** committed; include the rebuilt assets.
- Do not reset or overwrite existing changes unless the user explicitly asks.
- Current runtime facts:
  - execution core is `ClaudeSDKClient`; sessions/messages are SDK transcripts
    under `claude/projects/<agent_id>/<uuid>.jsonl`; there are no
    sessions/messages/runs tables;
  - `instructions` are appended to the Claude Code preset system prompt;
  - the default tool set is Claude Code's own (the runner does not set `tools`);
  - platform and skill tools are **not wired** into the SDK yet, and the
    per-agent `enabled_skills` list does not gate anything in this version;
  - skills are file documents under `skills/`; editing them does not require a
    form, but the skill tools they declare are unavailable;
  - memory is the shared user profile `claude/CLAUDE.md` plus per-Agent
    `workspaces/<agent_id>/CLAUDE.md`; Claude auto memory is displayed read-only;
    there is no `/memory` command, memory table, memory tool or consolidation
    skill;
  - rubric evaluation, QuickJS `eval`, session full-text search and Run history
    are removed;
  - browser screenshots are not pushed to WeChat until the tool layer is wired.
- Production uses systemd + venv + SQLite. Docker deployment is deprecated:
  do not reintroduce Docker Compose, Dockerfiles, nginx, or Postgres.
- If a commit workflow change is requested, update both `CLAUDE.md` and this
  skill before continuing.
- Include the exact test/build commands and their outcomes in the final response.
