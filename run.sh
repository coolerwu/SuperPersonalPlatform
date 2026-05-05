#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="super-personal-platform.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
RUN_DIR="${APP_DIR}/.run"

usage() {
  cat <<USAGE
Usage:
  ./run.sh dev
  ./run.sh prod
  ./run-dev.sh
  ./run-prod.sh

Environment:
  SUPER_PERSONAL_HOST    default: 0.0.0.0
  SUPER_PERSONAL_PORT    default: 8888
  SUPER_PERSONAL_CONFIG  default: config.yaml
USAGE
}

config_path() {
  local raw_config="${SUPER_PERSONAL_CONFIG:-config.yaml}"
  case "$raw_config" in
    /*) printf '%s\n' "$raw_config" ;;
    *) printf '%s\n' "${APP_DIR}/${raw_config}" ;;
  esac
}

ensure_config() {
  local resolved_config
  resolved_config="$(config_path)"
  if [[ ! -f "$resolved_config" ]]; then
    echo "Config file not found: ${resolved_config}" >&2
    echo "Copy config.example.yaml to config.yaml or set SUPER_PERSONAL_CONFIG." >&2
    exit 1
  fi
}

ensure_clean_git() {
  cd "$APP_DIR"
  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    echo "Git working tree is dirty. Commit or stash local changes before updating." >&2
    git status --short >&2
    exit 1
  fi
}

update_git() {
  cd "$APP_DIR"
  git pull --ff-only
}

ensure_venv() {
  cd "$APP_DIR"
  if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
    python3 -m venv "${APP_DIR}/.venv"
  fi
}

install_python_deps() {
  local target="$1"
  cd "$APP_DIR"
  "${APP_DIR}/.venv/bin/pip" install -e "$target"
}

pid_cwd() {
  local pid="$1"
  lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

stop_dev_port_processes() {
  local port="${SUPER_PERSONAL_PORT:-8888}"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  local pid cwd stopped=()
  for pid in $pids; do
    cwd="$(pid_cwd "$pid")"
    if [[ "$cwd" == "$APP_DIR" ]]; then
      echo "Stopping existing dev service on port ${port}: pid ${pid}"
      kill "$pid" 2>/dev/null || true
      stopped+=("$pid")
    fi
  done

  for pid in "${stopped[@]}"; do
    local remaining=20
    while kill -0 "$pid" 2>/dev/null && (( remaining > 0 )); do
      sleep 0.25
      remaining=$((remaining - 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "Force stopping dev service on port ${port}: pid ${pid}"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

run_dev() {
  ensure_config
  ensure_venv
  install_python_deps ".[dev]"
  stop_dev_port_processes

  cd "$APP_DIR"
  export SUPER_PERSONAL_HOST="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  export SUPER_PERSONAL_PORT="${SUPER_PERSONAL_PORT:-8888}"
  export SUPER_PERSONAL_CONFIG="$(config_path)"
  export SUPER_PERSONAL_RELOAD=1
  exec "${APP_DIR}/.venv/bin/python" -m server
}

write_service_file() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required for prod mode." >&2
    exit 1
  fi

  mkdir -p "$RUN_DIR"

  local host="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  local port="${SUPER_PERSONAL_PORT:-8888}"
  local resolved_config
  resolved_config="$(config_path)"
  local generated_service="${RUN_DIR}/${SERVICE_NAME}"

  cat >"$generated_service" <<SERVICE
[Unit]
Description=Super Personal Platform
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
Environment=SUPER_PERSONAL_HOST=${host}
Environment=SUPER_PERSONAL_PORT=${port}
Environment=SUPER_PERSONAL_CONFIG=${resolved_config}
ExecStart=${APP_DIR}/.venv/bin/python -m server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

  if ! sudo test -f "$SERVICE_PATH" || ! sudo cmp -s "$generated_service" "$SERVICE_PATH"; then
    sudo install -m 0644 "$generated_service" "$SERVICE_PATH"
    sudo systemctl daemon-reload
  fi
}

run_prod() {
  ensure_config
  ensure_clean_git
  update_git
  ensure_venv
  install_python_deps "."
  write_service_file

  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  sudo systemctl status "$SERVICE_NAME" --no-pager
}

main() {
  local mode="${1:-}"
  case "$mode" in
    dev)
      shift
      run_dev "$@"
      ;;
    prod)
      shift
      run_prod "$@"
      ;;
    help|--help|-h)
      usage
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
