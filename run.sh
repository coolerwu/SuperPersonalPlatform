#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="super-personal-platform.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PROD_GIT_URL="https://github.com/coolerwu/SuperPersonalPlatform.git"
PROD_GIT_BRANCH="main"
PROD_GIT_PULL_ATTEMPTS=3
PYTHON_DEPS_INSTALL_ATTEMPTS=3

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
  SUPER_PERSONAL_SERVICE_USER
                         prod systemd user; default: SUDO_USER or current user
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
  WORKSPACE_WAS_EXPLICIT=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --workspace)
        if [[ $# -lt 2 || -z "$2" ]]; then
          echo "--workspace requires a path." >&2
          exit 1
        fi
        WORKSPACE_DIR="$(absolute_path "$2")"
        WORKSPACE_WAS_EXPLICIT=1
        shift 2
        ;;
      --workspace=*)
        WORKSPACE_DIR="$(absolute_path "${1#--workspace=}")"
        WORKSPACE_WAS_EXPLICIT=1
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

git_in_repo() {
  git -c "safe.directory=${SCRIPT_DIR}" "$@"
}

git_https_in_repo() {
  git -c "safe.directory=${SCRIPT_DIR}" -c "http.version=HTTP/1.1" "$@"
}

ensure_config() {
  local resolved_config
  resolved_config="$(config_path)"
  if [[ ! -f "$resolved_config" ]]; then
    mkdir -p "$WORKSPACE_DIR"
    local root_config="${SCRIPT_DIR}/config.yaml"
    local legacy_prod_config="${HOME}/.super-personal-platform/config.yaml"
    if [[ "$WORKSPACE_WAS_EXPLICIT" == "0" && -f "$root_config" ]]; then
      cp "$root_config" "$resolved_config"
      echo "Created workspace config from project config: ${resolved_config}"
    elif [[ "${RUN_MODE:-}" == "prod" && "$WORKSPACE_WAS_EXPLICIT" == "0" && -f "$legacy_prod_config" ]]; then
      cp "$legacy_prod_config" "$resolved_config"
      echo "Created workspace config from existing prod config: ${resolved_config}"
    else
      cp "${SCRIPT_DIR}/config.example.yaml" "$resolved_config"
      echo "Created workspace config from template: ${resolved_config}"
    fi
  fi
}

ensure_clean_git() {
  cd "$SCRIPT_DIR"
  if [[ -n "$(git_in_repo status --porcelain --untracked-files=all)" ]]; then
    echo "Git working tree is dirty. Commit or stash local changes before updating." >&2
    git_in_repo status --short >&2
    exit 1
  fi
}

update_git() {
  cd "$SCRIPT_DIR"
  local before_head after_head
  before_head="$(git_in_repo rev-parse HEAD)"
  local attempt delay=2
  for ((attempt = 1; attempt <= PROD_GIT_PULL_ATTEMPTS; attempt += 1)); do
    echo "Pulling production code from ${PROD_GIT_URL} (${PROD_GIT_BRANCH}), attempt ${attempt}/${PROD_GIT_PULL_ATTEMPTS}"
    if git_https_in_repo pull --ff-only "$PROD_GIT_URL" "$PROD_GIT_BRANCH"; then
      after_head="$(git_in_repo rev-parse HEAD)"
      if [[ "$before_head" == "$after_head" ]]; then
        CODE_UPDATED=0
      else
        CODE_UPDATED=1
      fi
      return 0
    fi
    if [[ "$attempt" -lt "$PROD_GIT_PULL_ATTEMPTS" ]]; then
      echo "git pull failed; retrying in ${delay}s..." >&2
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done
  echo "git pull failed after ${PROD_GIT_PULL_ATTEMPTS} attempts." >&2
  return 1
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
  local attempt delay=2
  for ((attempt = 1; attempt <= PYTHON_DEPS_INSTALL_ATTEMPTS; attempt += 1)); do
    echo "Installing Python dependencies (${target}), attempt ${attempt}/${PYTHON_DEPS_INSTALL_ATTEMPTS}"
    if "${SCRIPT_DIR}/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir --retries 5 --timeout 60 -e "$target"; then
      return 0
    fi
    if [[ "$attempt" -lt "$PYTHON_DEPS_INSTALL_ATTEMPTS" ]]; then
      echo "pip install failed; retrying in ${delay}s..." >&2
      sleep "$delay"
      delay=$((delay * 2))
    fi
  done
  echo "pip install failed after ${PYTHON_DEPS_INSTALL_ATTEMPTS} attempts." >&2
  return 1
}

resolve_service_user() {
  if [[ -n "${SUPER_PERSONAL_SERVICE_USER:-}" ]]; then
    printf '%s\n' "$SUPER_PERSONAL_SERVICE_USER"
    return
  fi
  if [[ -n "${SUDO_USER:-}" ]]; then
    printf '%s\n' "$SUDO_USER"
    return
  fi
  id -un
}

resolve_service_group() {
  local user="$1"
  id -gn "$user" 2>/dev/null || true
}

require_non_interactive_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    return
  fi
  if sudo -n true 2>/dev/null; then
    return
  fi
  cat >&2 <<'MSG'
Production mode requires non-interactive sudo for systemctl/install commands.
Current environment cannot prompt for a password (for example when triggered by update-service).
Please run with a TTY once, or configure passwordless sudo for install/systemctl.
MSG
  exit 1
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
  RUN_MODE=dev
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
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
  local service_user service_group group_line=""
  service_user="$(resolve_service_user)"
  service_group="$(resolve_service_group "$service_user")"
  if [[ -n "$service_group" ]]; then
    group_line="Group=${service_group}"
  fi
  SERVICE_FILE_CHANGED=0

  cat >"$generated_service" <<SERVICE
[Unit]
Description=Super Personal Platform
After=network.target

[Service]
Type=simple
User=${service_user}
${group_line}
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

  if [[ -f "$SERVICE_PATH" ]] && cmp -s "$generated_service" "$SERVICE_PATH"; then
    echo "systemd service unchanged; skipping install and daemon-reload"
    return
  fi

  sudo install -m 0644 "$generated_service" "$SERVICE_PATH"
  sudo systemctl daemon-reload
  SERVICE_FILE_CHANGED=1
}

run_prod() {
  RUN_MODE=prod
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
  ensure_config
  ensure_clean_git
  update_git
  ensure_venv
  install_python_deps "."
  write_service_file
  local needs_sudo=0
  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" || "${CODE_UPDATED:-0}" == "1" ]]; then
    needs_sudo=1
  fi
  if [[ "$needs_sudo" == "0" ]]; then
    echo "No code or systemd unit changes; skipping systemctl enable/restart/status."
    return
  fi
  require_non_interactive_sudo

  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" ]]; then
    sudo systemctl enable "$SERVICE_NAME"
  else
    echo "systemd service unchanged; skipping enable"
  fi
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
