# Super Personal Platform

A compact personal platform served as one deployable Python application. The frontend is built with Vite and served by the FastAPI backend on port `8888`.

## Requirements

- Python 3.12.x
- Linux systemd + sudo for production deployment

## Setup

```bash
cp config.example.yaml config.yaml
./run-dev.sh
```

Open `http://localhost:8888` and log in with the token from `config.yaml`.

Development mode runs the current local working tree. It does not check git status or pull code. If the configured port is already held by a process whose working directory is this project, dev mode stops that process before starting.

## Production

```bash
./run-prod.sh
```

Production mode updates the git checkout with `git pull --ff-only`, prepares the Python virtualenv, registers or refreshes the systemd service, and restarts it. The frontend build output in `web/dist` is expected to be committed; the run scripts do not install or build frontend assets.

After login, use `系统 -> 更新服务` to trigger the same production update flow from the web UI.

## Project Architecture

`docs/project-architecture.md` is part of the project contract. Update it whenever implementation changes behavior, architecture, commands, or operating assumptions.
