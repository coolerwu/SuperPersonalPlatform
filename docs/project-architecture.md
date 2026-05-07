# Project Architecture

## Current Shape

- This is a new full-stack personal platform in `/Users/wulang/Desktop/AI/SuperPersonalPlatform`.
- The backend is Python 3.12.x with FastAPI and listens on port `8888`.
- The frontend is React + Vite and uses xterm.js for the browser terminal. Production uses the committed `web/dist` build output; run scripts do not install or build frontend assets.
- The app is not deployed as separated frontend/backend services. Browser traffic goes to FastAPI, and FastAPI serves both API and built frontend assets.
- Agent Chat uses LangGraph as the backend agent execution platform and LangChain as the infrastructure adapter for OpenAI-compatible external chat models.

## Backend Architecture

- The backend follows a standard COLA layout:
  - `server/adapter`: FastAPI routes, HTTP DTOs, static frontend serving.
  - `server/app`: application services and use-case orchestration.
  - `server/domain`: framework-free domain models, rules, and domain errors.
  - `server/infrastructure`: config loading, HTTP clients, session cookie implementation, app factory wiring.
- Domain code must not import FastAPI, httpx, or filesystem/config libraries.
- New behavior should be added through application services first, with adapters kept thin.

## Implemented Capabilities

- Single-token login using the active workspace `config.yaml` at `auth.token`. Login, auth-state checks, protected HTTP routes, and terminal WebSocket messages re-read the current workspace token, so changing `config.yaml` invalidates old sessions and allows the new token without a service restart.
- Login writes an HttpOnly cookie. Logout clears it.
- Auth state is available through `GET /api/auth/me`.
- Agent Chat uses the existing single-token login model. There is no Agent-specific permission switch, user model, role model, or per-Agent authorization; authenticated users can access Agent routes.
- Agent Chat configuration lives in workspace `config.yaml` under `llm.models`, `llm.default_model_id`, platform `tools`, legacy `common_skills.tools`, `agents.definitions`, and `agents.default_agent_id`. Model entries contain OpenAI-compatible `base_url`, `api_key`, `model`, optional `temperature`, and `supports_images`; Agent entries contain `id`, `name`, `system_prompt`, `model_id`, optional `skill_ids`, and optional per-Agent `tools` overrides.
- `GET /api/agents/options` is authenticated and returns Agent options with each Agent's bound model capability metadata without exposing `api_key`.
- `GET /api/agents/config` and `PUT /api/agents/config` are authenticated workspace configuration APIs for model, platform tool, legacy common skill tool, and Agent definitions. They mask model API keys in read responses; empty `api_key` values on update preserve existing keys.
- Agent tools follow an OpenClaw/Hermes-style split: tools are typed callable functions registered by the backend Tool Registry; skills are Markdown operating instructions visible only when bound through `skill_ids`. Platform and Agent `tools` config uses `profile`, `allow`, and `deny`; `deny` wins over profile and allow. The default profile exposes no tools by itself, the `self-dev` profile exposes skill, repo filesystem, runtime command, and git tools, and legacy `common_skills.tools` still enables `list_skill`/`read_skill` for backwards compatibility.
- `WebSocket /api/agents/chat/connect` is authenticated, accepts JSON `message` events with `agent_id`, `content`, and optional base64 `images`, and returns `status`, `checkpoint`, `assistant_message`, or `error` events. The backend chooses the model through the selected Agent's `model_id`; the frontend does not send `model_id`. Each request first runs an internal task-goal confirmation model call, then passes that goal as hidden context into the Agent response; the confirmed goal is also sent to the page as a compact checkpoint but is not persisted as conversation history. When resolved tools are available, the LangGraph workflow splits ReAct into separate `reason_skill_tools` and `act_skill_tools` states: reason uses LangChain native tool calling to request tool calls or produce the final answer, act dispatches calls through the Tool Registry and appends observations. The loop emits checkpoint events for goal confirmation, reason, tool action, and final-answer milestones, and runs up to 60 reason iterations before forcing a final answer.
- `/api/self-dev/*` routes are authenticated and manage self-development tasks under workspace `self-dev/tasks/{task_id}`. A task records `task.json`, `events.jsonl`, and a temporary cloned repo at `repo/`; the task branch is named `agent/self-dev-{task_id}`. Running a task invokes the selected Agent through the existing LangGraph/LangChain pipeline with repo-bound tool runtime context. Push is a separate confirmed action that commits all task repo changes and pushes the task branch, not `main`.
- `/api/proxy/site/` reverse-proxies the configured upstream site under `proxy.upstream_base_url`.
- The proxy forwards normal HTTP methods through the Python backend and returns upstream status, body, and safe response headers.
- `/api/system/*` routes are protected at the router level. `POST /api/system/config/read` reads the active workspace `config.yaml`, `PUT /api/system/config` validates and writes it, `POST /api/system/logs/list` and `POST /api/system/logs/read` expose read-only unified logs, `POST /api/system/update-service` starts a background production update task, and `/api/system/terminal/connect` exposes authenticated live terminal sessions.
- `WebSocket /api/system/terminal/connect` starts an interactive PTY shell on the backend machine. The terminal WebSocket uses JSON messages: client `input` messages write raw key data to the PTY, client `resize` messages update PTY rows and columns, and server `output` messages carry raw terminal output. The WebSocket is authenticated before accept and revalidates the session cookie against the current workspace `config.yaml` token for every client message. Terminal sessions are live-only; the platform does not persist terminal transcripts or expose terminal history APIs. The frontend terminal also provides a mobile-friendly auxiliary input bar (including password-mask toggle) so iPhone touch keyboards can reliably submit secret prompts and Enter.
- The frontend contains a login page, a full-height console app shell, a home overview, an Agent page, a self-development workbench page, an iframe-based Hermes UI proxy page, a terminal page, and a system page split into config, logs, and update tabs. Menu pages fill the right-side workspace directly without a repeated global page title/header or floating outer card; individual tools such as chat, logs, terminal output, and configuration groups keep their own local panels. Workbench-style pages use 100% available width and viewport-height sizing with `100dvh` fallbacks for mobile browser chrome. The Agent page has a chat tab and a workspace configuration tab for models, platform tools, and Agent personalities, and its chat/config workspace scrolls content inside the panel instead of stretching the whole page. A dedicated small-screen breakpoint (<=430px, including iPhone 14 width) further optimizes sidebar navigation, safe-area padding, chat composer stacking, and panel heights for one-hand mobile use.

## Operating Notes

- `AGENTS.md` is the repository-level Codex instruction entrypoint. It indexes this architecture document for project memory and operating assumptions.
- The project contains a local Codex skill at `.codex/skills/project-commit` for the standard test, architecture update, commit, and push workflow.
- `config.example.yaml` is the committed template for workspace configuration.
- `config.example.yaml` includes a default Agent Chat section. Local workspaces must replace model `api_key`, `base_url`, and `model` with real OpenAI-compatible provider values before using Agent Chat.
- Default proxy target is `http://192.168.1.3:9119/`.
- The proxy currently supports ordinary HTTP requests, not WebSocket upgrade traffic.
- The proxy HTTP client uses `trust_env=False` so system proxy settings do not intercept private LAN upstream requests.
- HTML, CSS, and JavaScript returned through the proxy get light rewriting for common root-relative paths so upstream assets and API calls continue through `/api/proxy/site/`.
- Unknown authenticated `/api/*` requests fall back to the upstream proxy after platform-owned API routes are checked, which supports embedded apps that call root-relative APIs such as `/api/status`.
- Known upstream root asset prefixes `/fonts/*`, `/ds-assets/*`, and `/dashboard-plugins/*` also fall back to the upstream proxy so embedded absolute asset paths do not hit the platform SPA fallback.
- Workspace `config.yaml` should stay local and must not be committed.
- The system page edits the active workspace `config.yaml` in place. Saved YAML is parsed and validated against required runtime settings before it replaces the file. Changing `auth.token` takes effect immediately for login and route authentication; existing cookies issued with the old token no longer authenticate.
- Agent routes re-read the active workspace `config.yaml` so model definitions and personality definitions can change without a service restart. API responses and unified request logs must not expose model `api_key` values.
- Workspace skills can live under AgentSkills-style `skills/common/{skill}/SKILL.md` and `skills/agents/{agent_id}/{skill}/SKILL.md`; legacy `skills/common/*.md` and `skills/agents/{agent_id}/*.md` files remain readable for compatibility. Skill ids use `common:{stem}` or `private:{stem}` and must be explicitly listed in the selected Agent's `skill_ids`; Agents cannot read unbound skills or another Agent's private skills.
- Agent chat supports per-message image inputs for models configured with `supports_images: true`. Images are sent as base64 data in the WebSocket message for the current request only, are included in the internal task-goal confirmation call and the tool-capable Agent response call, and are not written to the workspace.
- Workspace `logs/` contains unified platform log files named `platform-YYYY-MM-DD.log`; logs are read-only in the UI, default to the latest file, scroll to the tail when loaded, and are retained for 3 days by the system log service. The unified log includes update-service output and `/api/*` request summaries with method, path, status, duration, and client, but never request bodies.
- Workspace `self-dev/tasks/` contains self-development task directories with `task.json`, `events.jsonl`, and a temporary `repo/` clone. Task repos are operational workspaces for Agent tools and should not be treated as the running application code checkout.
- Terminal sessions are not persisted. Older workspaces may still contain legacy `terminal/sessions/` transcript files from previous builds, but current backend and frontend code no longer create, read, or display them.
- Workspace `.run/` contains runtime-only files such as update locks and generated service files, not durable logs or terminal history. If `.run/` is missing in production, it has no effect until an operation needs it; production startup or web-triggered update creates it automatically. Web-triggered update locks record the background update process PID, and stale legacy or dead-process locks are removed before starting a new update.
- The default workspace is `.super-personal-platform` under the repository directory for both dev and prod.
- If the default workspace has no `config.yaml`, `run.sh` first copies an existing repository-root `config.yaml`, then the former default `$HOME/.super-personal-platform/config.yaml` for prod, and finally the committed `config.example.yaml` template.
- Start development with `./run-dev.sh` or `./run.sh dev`.
- Development startup uses the default `.super-personal-platform` workspace. It does not run git checks or pull code; it is for the current local working tree. If the configured port is held by a process whose working directory is this project, dev startup stops it before launching.
- Pass `--workspace /path/to/workspace` to dev or prod to override the default workspace. A workspace stores `config.yaml`, unified logs, and `.run/` runtime data; code, `.venv`, frontend assets, and live terminal output stay outside durable workspace state.
- Deploy production with `./run-prod.sh` or `./run.sh prod`.
- Production startup uses the default `.super-personal-platform` workspace so dev and prod reuse the same workspace unless `--workspace` is specified.
- `run.sh` contains the dev/prod logic. `run-dev.sh` and `run-prod.sh` only forward to it.
- The Python service entrypoint is `.venv/bin/python -m server`; it wraps uvicorn internally.
- The service reads configuration from `${SUPER_PERSONAL_WORKSPACE}/config.yaml`. `SUPER_PERSONAL_CONFIG` is not supported.
- Production deployment requires Linux systemd and sudo for service changes and restarts. `run.sh prod` compares Git HEAD before/after pull and service-unit content; when both code and unit are unchanged it skips `systemctl enable/restart/status` entirely (no sudo needed). Only when restart/install is actually required does it validate non-interactive sudo availability (`sudo -n`) for web-triggered no-TTY updates. It pulls `main` from the public HTTPS repository `https://github.com/coolerwu/SuperPersonalPlatform.git` with command-scoped `safe.directory`, forces Git HTTPS pulls to HTTP/1.1, retries transient pull failures 3 times, installs Python dependencies with no pip cache and a 3-attempt outer retry for transient package-index failures, refreshes `super-personal-platform.service` only when the generated unit content differs, enables the unit only after a unit refresh, and restarts the service when code or service changes are detected. Web-triggered updates start `run-prod.sh` directly as a background process rather than through `systemd-run`.
- The production systemd unit runs as `${SUPER_PERSONAL_SERVICE_USER}` when set; otherwise it uses `${SUDO_USER}` when present, then falls back to the current terminal user from `id -un`. The unit also writes `Group=` when the user's primary group can be resolved. The browser terminal shell inherits this service user, so root deployments show `root@...`.
- Use the web UI at `Agent` to chat with configured personality Agents, `系统 -> 配置` to edit the active workspace configuration, `系统 -> 日志` to inspect unified logs in a fixed-height console viewer, `系统 -> 更新` to manually trigger the production update flow after login, and `终端` to open an authenticated live xterm.js shell on the backend machine without saving transcripts.
- Before committing changes, execute the local `$project-commit` skill.

## Maintenance Rule

- Every implementation pass must update this file if it changes architecture, behavior, setup, commands, dependencies, configuration, public interfaces, or operating assumptions.
