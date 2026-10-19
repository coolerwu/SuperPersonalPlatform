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
   - `cd apps/web && npm run build`
   - `apps/web/dist` is committed; include the rebuilt assets in the commit.
   Production does not run npm; the web image copies the committed dist into nginx.
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

Production deployment source is **GitHub pull** from `origin`. Do not deploy
from a developer working tree or a mutable `rc-clone`. The legacy systemd
service is not used for v1.

If the user explicitly asks to deploy v1 on a target machine:

1. Push the target branch to `origin` first and record the pushed SHA.
2. On the target host, check out/pull the branch into a v1 directory; do not
   overwrite the legacy checkout. Verify `git rev-parse HEAD` equals the pushed
   SHA and `git status --short` is clean.
3. Ensure `deploy/.env` exists on the target with `APP_AUTH_TOKEN`, a valid
   `LLM_API_KEY`, and `POSTGRES_PASSWORD`; never commit that file.
4. From the target repository root run:
   `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build`
5. Verify with:
   `docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps`
   and `curl --noproxy '*' http://127.0.0.1:${WEB_PORT:-8080}/api/health`.
6. Report pull/HEAD, build, service status, and health separately. Do not claim
   deployment success if any phase failed.
7. Direct tarball/image copy is only an explicit emergency fallback. If used,
   build from a committed SHA, exclude `.env`/`data/`/`.venv/`/`node_modules/`,
   deploy to a new directory, and record the SHA/hash.
8. Never put SSH/sudo passwords or API keys in repository files or command text.

## Guardrails

- This project intentionally commits all uncommitted code during this workflow.
- Always use `git add .`; do not stage files one by one.
- Do not commit `.env`, `data/`, `.super-personal-platform/`, `node_modules/`,
  `apps/web/dist/`, virtualenvs, caches, or dependency directories.
- Do not reset or overwrite existing changes unless the user explicitly asks.
- Do not assume a push synchronized `rc-clone`; it is an independent checkout.
- v1 exposes no file/shell/browser/skills/memory tools and has no approval flow;
  do not describe it as having those capabilities.
- If a commit workflow change is requested, update both `CLAUDE.md` and this
  skill before continuing.
- Include the exact test/build commands and their outcomes in the final response.
