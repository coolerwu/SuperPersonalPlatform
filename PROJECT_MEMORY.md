# Project Memory

## Current Shape

- This is a new full-stack personal platform in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.
- The backend is Python 3.12.x with FastAPI and listens on port `8888`.
- The frontend is React + Vite. Production deployment requires `npm run build` in `web/`; the backend serves `web/dist`.
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
- `GET /api/proxy/logs` fetches the configured upstream logs URL and normalizes JSON or text into a frontend-friendly payload.
- The frontend contains a login page, an app shell, a home overview, and an embedded proxy logs page.

## Operating Notes

- Copy `config.example.yaml` to `config.yaml` before running locally.
- Default proxy target is `http://192.168.1.3:9119/logs`.
- `config.yaml` should stay local and must not be committed.
- Build frontend before backend deployment: `cd web && npm run build`.
- Start the platform with `./run.sh`.

## Maintenance Rule

- Every implementation pass must update this file if it changes architecture, behavior, setup, commands, dependencies, configuration, or public interfaces.
