#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="super-personal-platform.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

usage() {
  cat <<USAGE
Usage:
  ./run.sh dev [--workspace PATH]
  ./run.sh prod [--workspace PATH]
  ./run-dev.sh [--workspace PATH]
  ./run-prod.sh [--workspace PATH]

Environment:
  SUPER_PERSONAL_HOST    default: 0.0.0.0
  SUPER_PERSONAL_PORT    default: 8888
USAGE
}

absolute_path() {
  local raw_path="$1"
  case "$raw_path" in
    /*) printf '%s\n' "$raw_path" ;;
    *) printf '%s\n' "$(pwd -P)/${raw_path}" ;;
  esac
}

parse_workspace() {
  local default_workspace="$1"
  shift
  WORKSPACE_DIR="$default_workspace"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --workspace)
        if [[ $# -lt 2 || -z "$2" ]]; then
          echo "--workspace requires a path." >&2
          exit 1
        fi
        WORKSPACE_DIR="$(absolute_path "$2")"
        shift 2
        ;;
      --workspace=*)
        WORKSPACE_DIR="$(absolute_path "${1#--workspace=}")"
        shift
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done
}

config_path() {
  printf '%s\n' "${WORKSPACE_DIR}/config.yaml"
}

ensure_config() {
  local resolved_config
  resolved_config="$(config_path)"
  if [[ ! -f "$resolved_config" ]]; then
    mkdir -p "$WORKSPACE_DIR"
    cp "${SCRIPT_DIR}/config.example.yaml" "$resolved_config"
    echo "Created workspace config from template: ${resolved_config}"
  fi
}

ensure_clean_git() {
  cd "$SCRIPT_DIR"
  if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    echo "Git working tree is dirty. Commit or stash local changes before updating." >&2
    git status --short >&2
    exit 1
  fi
}

update_git() {
  cd "$SCRIPT_DIR"
  git pull --ff-only
}

ensure_venv() {
  cd "$SCRIPT_DIR"
  if [[ ! -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    python3 -m venv "${SCRIPT_DIR}/.venv"
  fi
}

install_python_deps() {
  local target="$1"
  cd "$SCRIPT_DIR"
  "${SCRIPT_DIR}/.venv/bin/pip" install -e "$target"
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
    if [[ "$cwd" == "$SCRIPT_DIR" ]]; then
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
  parse_workspace "$(pwd -P)" "$@"
  ensure_config
  ensure_venv
  install_python_deps ".[dev]"
  stop_dev_port_processes

  cd "$SCRIPT_DIR"
  export SUPER_PERSONAL_HOST="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  export SUPER_PERSONAL_PORT="${SUPER_PERSONAL_PORT:-8888}"
  export SUPER_PERSONAL_WORKSPACE="$WORKSPACE_DIR"
  export SUPER_PERSONAL_RELOAD=1
  exec "${SCRIPT_DIR}/.venv/bin/python" -m server
}

write_service_file() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required for prod mode." >&2
    exit 1
  fi

  local run_dir="${WORKSPACE_DIR}/.run"
  mkdir -p "$run_dir"

  local host="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  local port="${SUPER_PERSONAL_PORT:-8888}"
  local generated_service="${run_dir}/${SERVICE_NAME}"

  cat >"$generated_service" <<SERVICE
[Unit]
Description=Super Personal Platform
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
Environment=SUPER_PERSONAL_HOST=${host}
Environment=SUPER_PERSONAL_PORT=${port}
Environment=SUPER_PERSONAL_WORKSPACE=${WORKSPACE_DIR}
ExecStart=${SCRIPT_DIR}/.venv/bin/python -m server
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
  parse_workspace "$(pwd -P)" "$@"
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
