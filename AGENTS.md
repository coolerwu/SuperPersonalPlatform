# Project Agent Instructions

`PROJECT_MEMORY.md` is not a Codex default configuration file. It is this
project's long-term memory document.

Before implementing changes in this repository:

- Read `PROJECT_MEMORY.md`.
- Run `git status --short` and account for existing worktree changes.
- Do not stage, commit, revert, or overwrite unrelated existing changes.

Keep `PROJECT_MEMORY.md` updated whenever implementation changes behavior,
architecture, commands, dependencies, configuration, public interfaces, or
operating assumptions.

Before committing project changes, run:

- `.venv/bin/python -m pytest`
- `cd web && npm test`
- `cd web && npm run build`

