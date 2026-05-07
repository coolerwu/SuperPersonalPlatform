# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Development server (port 8888)
./run-dev.sh

# Production deployment (Linux systemd required)
./run-prod.sh

# Run backend tests
.venv/bin/python -m pytest

# Run single test
.venv/bin/python -m pytest server/tests/test_file.py::test_name -v

# Frontend tests and build
cd web && npm test
cd web && npm run build
```

## Architecture Overview

This is a full-stack personal platform with FastAPI backend (port 8888) and React+Vite frontend. The backend follows COLA architecture:

- `server/adapter/` - FastAPI routes, HTTP DTOs, static file serving
- `server/app/` - Application services and use-case orchestration
- `server/domain/` - Framework-free domain models (no FastAPI, httpx, or filesystem imports)
- `server/infrastructure/` - Config loading, HTTP clients, session implementation, app factory

Agent Chat uses LangGraph for execution and LangChain for OpenAI-compatible LLM adapters.

## Workspace and Configuration

The workspace (default: `.super-personal-platform/`) holds:
- `config.yaml` - Auth token, LLM models, agent definitions, proxy settings
- `logs/` - Unified platform logs (3-day retention)
- `.run/` - Runtime locks and generated service files

Key config sections:
- `auth.token` - Single-token login
- `llm.models` - OpenAI-compatible model configs (base_url, api_key, model, supports_images)
- `agents.definitions` - Agent personalities with system_prompt, model_id, skill_ids, tools
- `tools.profile/allow/deny` - Tool exposure control

Changing `auth.token` takes effect immediately; existing cookies with old token are invalidated.

## Agent Chat System

- Agents are configured in workspace `config.yaml`
- Skills live in `skills/common/{skill}/SKILL.md` or `skills/agents/{agent_id}/{skill}/SKILL.md`
- Skill IDs: `common:{stem}` or `private:{stem}`
- WebSocket at `/api/agents/chat/connect` for chat
- Per-message image support for models with `supports_images: true`

## Commit Workflow

Before committing changes, execute the local `$project-commit` skill which:
1. Reads `AGENTS.md` and `docs/project-architecture.md`
2. Runs `git status --short`
3. Updates documentation if behavior/architecture changed
4. Runs tests: `.venv/bin/python -m pytest`, `cd web && npm test`, `cd web && npm run build`
5. Stages all with `git add .`
6. Commits and pushes

## Frontend Design Notes

- Menu pages should not have repeated global page titles/headers; the sidebar already shows location
- Workbench pages use 100% width with `100dvh` fallbacks
- Production uses committed `web/dist/` build output; run scripts do not build frontend assets
