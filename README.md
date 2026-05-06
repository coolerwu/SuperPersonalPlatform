# Super Personal Platform

A compact personal platform served as one deployable Python application. The frontend is built with Vite and served by the FastAPI backend on port `8888`.

## Requirements

- Python 3.12.x
- Linux systemd + sudo for production deployment

## Setup

```bash
./run-dev.sh
```

Open `http://localhost:8888` and log in with the token from `config.yaml`.

Development mode uses `.super-personal-platform` under the project directory as its workspace by default. The workspace holds `config.yaml`, logs, terminal transcripts, and runtime files. It does not check git status or pull code. If the configured port is already held by a process whose working directory is this project, dev mode stops that process before starting.
If the default workspace does not contain `config.yaml`, the script first reuses an existing project `config.yaml` when present; otherwise it creates one from `config.example.yaml`.

To use a different workspace:

```bash
mkdir -p /path/to/workspace
./run-dev.sh --workspace /path/to/workspace
```

## Production

```bash
./run-prod.sh
```

Production mode uses `.super-personal-platform` under the project directory as its workspace by default. It updates the git checkout with `git pull --ff-only`, prepares the Python virtualenv, registers or refreshes the systemd service with the current workspace path, and restarts it. The frontend build output in `web/dist` is expected to be committed; the run scripts do not install or build frontend assets.
If the default production workspace does not contain `config.yaml`, the script first reuses an existing project `config.yaml` when present, then tries `~/.super-personal-platform/config.yaml`, and otherwise creates one from `config.example.yaml`.

To deploy with a different workspace:

```bash
./run-prod.sh --workspace /path/to/workspace
```

After login, use `系统 -> 更新` to trigger the same production update flow from the web UI, or `终端` to open an authenticated xterm.js shell on the backend machine. Terminal transcripts are saved under the active workspace at `terminal/sessions/` and can be deleted from the terminal history list.

The production systemd service runs as `SUPER_PERSONAL_SERVICE_USER` when set; otherwise it uses `SUDO_USER` or the current `id -un` user. The web terminal shell inherits that service user.

## Project Architecture

`docs/project-architecture.md` is part of the project contract. Update it whenever implementation changes behavior, architecture, commands, or operating assumptions.
