# Remove Built-in Agents and Skills Design

## Goal

Remove every platform-defined Agent and Skill. Agent and Skill definitions must come only from the active workspace and remain editable and removable by the user.

## Architecture

- Delete the `BUILTIN_AGENTS`, `BUILTIN_SKILLS`, built-in override, automatic injection, fallback tool binding, and `is_builtin` API/UI concepts.
- `GET /api/agents/options` and `GET /api/agents/config` return only workspace-defined Agents and Skills.
- `PUT /api/agents/config` persists only ordinary Agent and Skill index definitions. The legacy `agents.builtin_overrides` field is unsupported.
- Tool Registry remains platform code because it describes executable capabilities, not Agent or Skill instances.

## Portfolio Binding

- Add `portfolio.agent_id` to workspace configuration as the explicit Agent binding for the Portfolio AI chat.
- The Portfolio page reads that binding from the backend. It never chooses or hardcodes a Skill.
- The bound ordinary Agent selects its own Skills through `agents.definitions[].skill_ids`.
- The ordinary `common:portfolio` Skill selects portfolio CRUD tools through `SKILL.md` frontmatter `tools.allow`.
- If `portfolio.agent_id` is empty, references a missing Agent, or that Agent lacks the required Skill/tool setup, the Portfolio holdings UI remains available while AI chat shows a configuration error and does not fall back to another Agent.

## Workspace Migration

Production migration converts the current investment preset into ordinary workspace data:

- Add or retain an ordinary `ai-investment-advisor` entry under `agents.definitions`.
- Add or retain `common:portfolio` under `skills.definitions`.
- Store the four portfolio Registry names in `skills/common/portfolio/SKILL.md` under `tools.allow`.
- Set `portfolio.agent_id: ai-investment-advisor`.
- Remove `agents.builtin_overrides` and all legacy built-in markers.

This is a one-time runtime data migration, not a compatibility layer. New workspaces do not receive automatic Agent or Skill presets.

## Frontend Behavior

- Agent and Skill management no longer show “内置” badges or disable editing/deletion based on `is_builtin`.
- Portfolio chat uses the configured `portfolio.agent_id` for sessions and WebSocket messages.
- Missing or invalid binding produces a concise configuration-state panel; portfolio CRUD remains usable.

## Validation

- Backend tests prove no Agents or Skills are injected into an empty workspace configuration.
- Config and API tests prove `portfolio.agent_id` is returned and invalid references are rejected or reported explicitly.
- Frontend tests prove management entries are ordinary and Portfolio chat uses the configured Agent rather than `ai-investment-advisor` hardcoding.
- Full Python tests, frontend tests, production build, browser QA, production migration, restart, and HTTP health verification are required before completion.
