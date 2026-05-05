# Project Architecture

## Current Shape

- This is a new full-stack personal platform in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.
- The backend is Python 3.12.x with FastAPI and listens on port `8888`.
- The frontend is React + Vite. Production uses the committed `web/dist` build output; run scripts do not install or build frontend assets.
- The app is not deployed as separated frontend/backend services. Browser traffic goes to FastAPI, and FastAPI serves both API and built frontend assets.

## Backend Architecture

- The backend follows a standard COLA layout:
  - `server/adapter`: FastAPI routes, HTTP DTOs, static frontend serving.
  - `server/app`: application services and use-case orchestration.
  - `server/domain`: framework-free domain models, rules, and domain errors.
  - `server/infrastructure`: config loading, HTTP clients, session cookie implementation, app factory wiring.
- Domain code must not import FastAPI, httpx, or filesystem/config libraries.
- New behavior should be added through application services first, with adapters kept thin.

## Implemented Capabilities

- Single-token login using `config.yaml` at `auth.token`.
- Login writes an HttpOnly cookie. Logout clears it.
- Auth state is available through `GET /api/auth/me`.
- `/api/proxy/site/` reverse-proxies the configured upstream site under `proxy.upstream_base_url`.
- The proxy forwards normal HTTP methods through the Python backend and returns upstream status, body, and safe response headers.
- `POST /api/system/update-service` starts a protected background production update task.
- The frontend contains a login page, an app shell, a home overview, an iframe-based proxy page, and a system page with an update-service button.

## Operating Notes

- `AGENTS.md` is the repository-level Codex instruction entrypoint. It indexes this architecture document for project memory and operating assumptions.
- The project contains a local Codex skill at `.codex/skills/project-commit` for the standard test, architecture update, commit, and push workflow.
- Copy `config.example.yaml` to `config.yaml` before running locally.
- Default proxy target is `http://192.168.1.3:9119/`.
- The proxy currently supports ordinary HTTP requests, not WebSocket upgrade traffic.
- The proxy HTTP client uses `trust_env=False` so system proxy settings do not intercept private LAN upstream requests.
- HTML, CSS, and JavaScript returned through the proxy get light rewriting for common root-relative paths so upstream assets and API calls continue through `/api/proxy/site/`.
- Unknown authenticated `/api/*` requests fall back to the upstream proxy after platform-owned API routes are checked, which supports embedded apps that call root-relative APIs such as `/api/status`.
- Known upstream root asset prefixes `/fonts/*`, `/ds-assets/*`, and `/dashboard-plugins/*` also fall back to the upstream proxy so embedded absolute asset paths do not hit the platform SPA fallback.
- `config.yaml` should stay local and must not be committed.
- Start development with `./run-dev.sh` or `./run.sh dev`.
- Development startup does not run git checks or pull code; it is for the current local working tree. If the configured port is held by a process whose working directory is this project, dev startup stops it before launching.
- Deploy production with `./run-prod.sh` or `./run.sh prod`.
- `run.sh` contains the dev/prod logic. `run-dev.sh` and `run-prod.sh` only forward to it.
- The Python service entrypoint is `.venv/bin/python -m server`; it wraps uvicorn internally.
- Production deployment requires Linux systemd and sudo. It registers or refreshes `super-personal-platform.service`, enables it, and restarts it.
- Use the web UI at `系统 -> 更新服务` to manually trigger the production update flow after login.
- Before committing changes, execute the local `$project-commit` skill.

## Maintenance Rule

- Every implementation pass must update this file if it changes architecture, behavior, setup, commands, dependencies, configuration, public interfaces, or operating assumptions.

