#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="super-personal-platform.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
SUDOERS_PATH="/etc/sudoers.d/super-personal-platform"
PROD_GIT_URL="https://github.com/coolerwu/SuperPersonalPlatform.git"
PROD_GIT_BRANCH="main"
PROD_GIT_PULL_ATTEMPTS=3
PYTHON_DEPS_INSTALL_ATTEMPTS=3
PYTHON_DEPS_STAMP_PREFIX=".super-personal-platform-python-deps"
PLAYWRIGHT_BROWSER_INSTALL_ATTEMPTS=3

usage() {
  cat <<USAGE
Usage:
  ./run.sh dev [--workspace PATH]
  ./run.sh prod [--workspace PATH]
  ./run.sh setup-sudo
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

has_systemctl() {
  command -v systemctl >/dev/null 2>&1
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

python_deps_stamp_path() {
  local name="$1"
  printf '%s\n' "${SCRIPT_DIR}/.venv/${PYTHON_DEPS_STAMP_PREFIX}-${name}.sha256"
}

python_deps_fingerprint() {
  local target="$1"
  cd "$SCRIPT_DIR"
  "${SCRIPT_DIR}/.venv/bin/python" - "$target" <<'PY'
import hashlib
from pathlib import Path
import sys

root = Path.cwd()
target = sys.argv[1]
dependency_files = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "requirements-dev.txt",
)

digest = hashlib.sha256()
digest.update(sys.version.encode("utf-8"))
digest.update(b"\0")
digest.update(target.encode("utf-8"))
digest.update(b"\0")

for relative_path in dependency_files:
    path = root / relative_path
    if not path.is_file():
        continue
    digest.update(relative_path.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")

print(digest.hexdigest())
PY
}

install_python_deps() {
  local target="$1"
  local name="$2"
  cd "$SCRIPT_DIR"
  local stamp_path expected_fingerprint current_fingerprint
  stamp_path="$(python_deps_stamp_path "$name")"
  expected_fingerprint="$(python_deps_fingerprint "$target")"
  if [[ -f "$stamp_path" ]]; then
    current_fingerprint="$(<"$stamp_path")"
    if [[ "$current_fingerprint" == "$expected_fingerprint" ]]; then
      echo "Python dependencies unchanged (${target}); skipping install."
      return 0
    fi
  fi

  local attempt delay=2
  for ((attempt = 1; attempt <= PYTHON_DEPS_INSTALL_ATTEMPTS; attempt += 1)); do
    echo "Installing Python dependencies (${target}), attempt ${attempt}/${PYTHON_DEPS_INSTALL_ATTEMPTS}"
    if "${SCRIPT_DIR}/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir --retries 5 --timeout 60 -e "$target"; then
      printf '%s\n' "$expected_fingerprint" >"$stamp_path"
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

ensure_playwright_chromium() {
  cd "$SCRIPT_DIR"
  if ! "${SCRIPT_DIR}/.venv/bin/python" - <<'PY'
from pathlib import Path
try:
    from playwright.sync_api import sync_playwright
except Exception:
    raise SystemExit(1)

with sync_playwright() as playwright:
    path = Path(playwright.chromium.executable_path)
    raise SystemExit(0 if path.exists() else 1)
PY
  then
    local attempt delay=2
    for ((attempt = 1; attempt <= PLAYWRIGHT_BROWSER_INSTALL_ATTEMPTS; attempt += 1)); do
      echo "Installing Playwright Chromium, attempt ${attempt}/${PLAYWRIGHT_BROWSER_INSTALL_ATTEMPTS}"
      if "${SCRIPT_DIR}/.venv/bin/python" -m playwright install chromium; then
        return 0
      fi
      if [[ "$attempt" -lt "$PLAYWRIGHT_BROWSER_INSTALL_ATTEMPTS" ]]; then
        echo "Playwright Chromium install failed; retrying in ${delay}s..." >&2
        sleep "$delay"
        delay=$((delay * 2))
      fi
    done
    echo "Playwright Chromium install failed after ${PLAYWRIGHT_BROWSER_INSTALL_ATTEMPTS} attempts." >&2
    return 1
  fi
  echo "Playwright Chromium already installed; skipping install."
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

require_sudo() {
  local reason="$1"
  if [[ "${EUID}" -eq 0 ]]; then
    return
  fi
  if sudo -n true 2>/dev/null; then
    return
  fi
  if [[ -t 0 ]]; then
    echo "sudo is required for ${reason}; prompting once."
    sudo -v
    return
  fi
  cat >&2 <<MSG
Production mode needs sudo for ${reason}, but this environment cannot prompt for a password.
Run './run.sh setup-sudo' once from a TTY to allow web-triggered restarts, or run './run.sh prod' from a terminal.
MSG
  exit 1
}

can_restart_without_prompt() {
  if [[ "${EUID}" -eq 0 ]]; then
    return 0
  fi
  local systemctl_path
  systemctl_path="$(command -v systemctl)"
  sudo -n -l "$systemctl_path" restart "$SERVICE_NAME" >/dev/null 2>&1
}

trigger_restart_by_service_exit() {
  if ! has_systemctl; then
    echo "systemctl is required to find the running service process for exit-based restart." >&2
    return 1
  fi
  local main_pid
  main_pid="$(systemctl show "$SERVICE_NAME" --property=MainPID --value 2>/dev/null || true)"
  if [[ -z "$main_pid" || "$main_pid" == "0" ]]; then
    echo "Cannot trigger exit-based restart because ${SERVICE_NAME} has no active MainPID." >&2
    return 1
  fi
  echo "Triggering systemd Restart=always by sending SIGTERM to ${SERVICE_NAME} MainPID=${main_pid}."
  kill -TERM "$main_pid"
}

install_restart_sudoers() {
  if ! has_systemctl; then
    echo "systemctl is required for setup-sudo." >&2
    exit 1
  fi
  if ! command -v visudo >/dev/null 2>&1; then
    echo "visudo is required for setup-sudo." >&2
    exit 1
  fi

  local service_user systemctl_path temp_file
  service_user="$(resolve_service_user)"
  systemctl_path="$(command -v systemctl)"
  temp_file="$(mktemp)"
  cat >"$temp_file" <<SUDOERS
# Managed by SuperPersonalPlatform run.sh.
# Allows web-triggered code updates to restart the existing service unit.
${service_user} ALL=(root) NOPASSWD: ${systemctl_path} restart ${SERVICE_NAME}
${service_user} ALL=(root) NOPASSWD: ${systemctl_path} status ${SERVICE_NAME} --no-pager
${service_user} ALL=(root) NOPASSWD: ${systemctl_path} is-active ${SERVICE_NAME}
SUDOERS

  visudo -cf "$temp_file"
  require_sudo "installing ${SUDOERS_PATH}"
  sudo install -m 0440 "$temp_file" "$SUDOERS_PATH"
  rm -f "$temp_file"
  echo "Installed limited sudoers rule at ${SUDOERS_PATH} for ${service_user}."
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

build_frontend_assets() {
  cd "${SCRIPT_DIR}/web"
  echo "Building frontend assets with npm run build."
  npm run build
}

run_dev() {
  RUN_MODE=dev
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
  ensure_config
  build_frontend_assets
  ensure_venv
  install_python_deps ".[dev]" "dev"
  ensure_playwright_chromium
  stop_dev_port_processes

  cd "$SCRIPT_DIR"
  export SUPER_PERSONAL_HOST="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  export SUPER_PERSONAL_PORT="${SUPER_PERSONAL_PORT:-8888}"
  export SUPER_PERSONAL_WORKSPACE="$WORKSPACE_DIR"
  export SUPER_PERSONAL_RELOAD=1
  export SUPER_PERSONAL_DEV_AUTH_BYPASS="${SUPER_PERSONAL_DEV_AUTH_BYPASS:-1}"
  exec "${SCRIPT_DIR}/.venv/bin/python" -m server
}

write_service_file() {
  if ! has_systemctl; then
    echo "systemctl is required for prod mode." >&2
    exit 1
  fi

  local host="${SUPER_PERSONAL_HOST:-0.0.0.0}"
  local port="${SUPER_PERSONAL_PORT:-8888}"
  local generated_service
  generated_service="$(mktemp "${TMPDIR:-/tmp}/super-personal-platform.service.XXXXXX")"
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
KillMode=process

[Install]
WantedBy=multi-user.target
SERVICE

  if [[ -f "$SERVICE_PATH" ]] && cmp -s "$generated_service" "$SERVICE_PATH"; then
    echo "systemd service unchanged; skipping install and daemon-reload"
    rm -f "$generated_service"
    return
  fi

  if [[ ! -t 0 ]] && ! sudo -n true 2>/dev/null; then
    SERVICE_FILE_REFRESH_SKIPPED=1
    echo "systemd service file changed, but this no-TTY update cannot install it without broader sudo; skipping unit refresh for this run."
    rm -f "$generated_service"
    return
  fi

  require_sudo "installing or reloading the systemd service file"
  sudo install -m 0644 "$generated_service" "$SERVICE_PATH"
  rm -f "$generated_service"
  sudo systemctl daemon-reload
  SERVICE_FILE_CHANGED=1
}

run_prod() {
  RUN_MODE=prod
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
  ensure_config
  ensure_clean_git
  update_git
  RESTART_BY_EXIT=0
  if ! can_restart_without_prompt && [[ ! -t 0 ]]; then
    RESTART_BY_EXIT=1
    cat <<'MSG'
This no-TTY update cannot run passwordless sudo for systemctl restart.
The update will install dependencies, skip systemd unit refresh/status, then terminate the current service process so systemd Restart=always starts the code currently on disk.
MSG
  fi
  ensure_venv
  install_python_deps "." "prod"
  ensure_playwright_chromium

  if ! has_systemctl; then
    if [[ "${CODE_UPDATED:-0}" == "1" ]]; then
      echo "systemctl not available; code and dependencies updated, but service restart must be done manually." >&2
    else
      echo "systemctl not available; no code changes detected, service restart skipped." >&2
    fi
    return 0
  fi

  if [[ "${RESTART_BY_EXIT:-0}" == "1" ]]; then
    echo "Skipping systemd unit refresh/status because restart is using the service-exit fallback."
    trigger_restart_by_service_exit
    return
  fi
  write_service_file
  local needs_sudo=0
  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" || "${SERVICE_FILE_REFRESH_SKIPPED:-0}" == "1" || "${CODE_UPDATED:-0}" == "1" ]]; then
    needs_sudo=1
  fi
  if [[ "$needs_sudo" == "0" ]]; then
    echo "No code or systemd unit changes; skipping systemctl enable/restart/status."
    return
  fi
  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" ]]; then
    require_sudo "systemctl enable/restart/status"
    sudo systemctl enable "$SERVICE_NAME"
  else
    echo "systemd service unchanged; skipping enable"
  fi
  echo "Restarting ${SERVICE_NAME} because code_updated=${CODE_UPDATED:-0} service_file_changed=${SERVICE_FILE_CHANGED:-0}."
  sudo systemctl restart "$SERVICE_NAME"
  echo "Restart command completed for ${SERVICE_NAME}; fetching service status."
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
    setup-sudo)
      shift
      if [[ $# -gt 0 ]]; then
        echo "setup-sudo does not accept arguments." >&2
        usage >&2
        exit 1
      fi
      install_restart_sudoers
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
