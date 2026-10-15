#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="super-personal-platform.service"
# F12: the unit lives in the invoking user's user-level systemd directory, so
# restart/status/enable never need sudo.
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
  ./run.sh prod [--workspace PATH] [--no-pull]
  ./run.sh rollback <tag|branch|commit> [--workspace PATH]
  ./run-dev.sh [--workspace PATH]
  ./run-prod.sh [--workspace PATH] [--no-pull]

Modes:
  prod      Pull the production branch, install dependencies, and restart the service.
  rollback  Check out a tag/branch/commit, then deploy it with the prod pipeline.
            Run ./run-prod.sh again afterwards to return to the production branch.
  --no-pull Skip the git pull in prod mode (offline redeploy or pinned commit).

The service unit is installed into the invoking user's user-level systemd
directory, so restart, status and enable never require sudo.

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
      --no-pull)
        PROD_SKIP_PULL=1
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
  if ! git_in_repo symbolic-ref -q HEAD >/dev/null 2>&1; then
    echo "Detached HEAD detected (a previous rollback); checking out ${PROD_GIT_BRANCH} before pull."
    git_in_repo checkout "$PROD_GIT_BRANCH"
  fi
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

service_user_home() {
  local user="$1" home=""
  home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
  printf '%s\n' "${home:-${HOME}}"
}

service_unit_path() {
  local user="$1"
  printf '%s\n' "$(service_user_home "$user")/.config/systemd/user/${SERVICE_NAME}"
}

ensure_user_systemd_env() {
  if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  fi
}

user_systemctl() {
  ensure_user_systemd_env
  systemctl --user "$@"
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
Run './run.sh prod' once from a terminal so this one-time step can complete.
MSG
  exit 1
}

can_restart_without_prompt() {
  if ! has_systemctl; then
    return 1
  fi
  user_systemctl show-environment >/dev/null 2>&1
}

ensure_linger() {
  local user="$1" linger
  linger="$(loginctl show-user "$user" -p Linger --value 2>/dev/null || true)"
  if [[ "$linger" == "yes" ]]; then
    echo "User systemd linger already enabled for ${user}."
    return 0
  fi
  require_sudo "enabling systemd linger for ${user}"
  sudo loginctl enable-linger "$user"
  echo "Enabled user systemd linger for ${user}; the service now starts on boot."
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
  if [[ "${PROD_SKIP_PULL:-0}" == "1" ]]; then
    echo "--no-pull is only valid for prod and rollback modes." >&2
    exit 1
  fi
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
  local service_user service_path service_dir
  service_user="$(resolve_service_user)"
  service_path="$(service_unit_path "$service_user")"
  service_dir="$(dirname "$service_path")"
  SERVICE_FILE_CHANGED=0

  local generated_service
  generated_service="$(mktemp "${TMPDIR:-/tmp}/super-personal-platform.service.XXXXXX")"
  cat >"$generated_service" <<SERVICE
[Unit]
Description=Super Personal Platform
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
Environment=SUPER_PERSONAL_HOST=${host}
Environment=SUPER_PERSONAL_PORT=${port}
Environment=SUPER_PERSONAL_WORKSPACE=${WORKSPACE_DIR}
ExecStart=${SCRIPT_DIR}/.venv/bin/python -m server
Restart=always
RestartSec=5
KillMode=process

[Install]
WantedBy=default.target
SERVICE

  if [[ -f "$service_path" ]] && cmp -s "$generated_service" "$service_path"; then
    echo "systemd user service unchanged; skipping install and daemon-reload"
    rm -f "$generated_service"
    return
  fi

  mkdir -p "$service_dir"
  install -m 0644 "$generated_service" "$service_path"
  rm -f "$generated_service"
  user_systemctl daemon-reload
  SERVICE_FILE_CHANGED=1
  echo "Installed user-level systemd unit at ${service_path}."
}

run_prod() {
  RUN_MODE=prod
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
  ensure_config
  ensure_clean_git
  if [[ "${PROD_SKIP_PULL:-0}" == "1" ]]; then
    echo "Skipping git pull because --no-pull was passed."
    CODE_UPDATED=1
  else
    update_git
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

  local service_user
  service_user="$(resolve_service_user)"
  if [[ "$service_user" != "$(id -un)" ]]; then
    cat >&2 <<MSG
User-level systemd only manages the calling user's own service.
Resolved service user is ${service_user}, but this script is running as $(id -un).
Run './run.sh prod' as ${service_user} without sudo; the linger step prompts for
sudo on its own if it is not enabled yet.
MSG
    exit 1
  fi
  ensure_linger "$service_user"
  if ! can_restart_without_prompt; then
    cat >&2 <<MSG
The user-level systemd manager for ${service_user} is not reachable, so the
service cannot be installed, enabled or restarted. Run
'loginctl enable-linger ${service_user}' and retry './run.sh prod'.
MSG
    exit 1
  fi
  write_service_file
  local needs_restart=0
  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" || "${CODE_UPDATED:-0}" == "1" ]]; then
    needs_restart=1
  fi
  if [[ "$needs_restart" == "0" ]]; then
    echo "No code or systemd unit changes; skipping systemctl enable/restart/status."
    return
  fi
  if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" ]]; then
    user_systemctl enable "$SERVICE_NAME"
  else
    echo "systemd user service unchanged; skipping enable"
  fi
  echo "Restarting ${SERVICE_NAME} because code_updated=${CODE_UPDATED:-0} service_file_changed=${SERVICE_FILE_CHANGED:-0}."
  user_systemctl restart "$SERVICE_NAME"
  echo "Restart command completed for ${SERVICE_NAME}; fetching service status."
  user_systemctl status "$SERVICE_NAME" --no-pager
}

run_rollback() {
  local target_ref="${1:-}"
  if [[ -z "$target_ref" || "$target_ref" == -* ]]; then
    echo "rollback requires a target tag, branch, or commit." >&2
    usage >&2
    exit 1
  fi
  shift

  RUN_MODE=prod
  parse_workspace "${SCRIPT_DIR}/.super-personal-platform" "$@"
  ensure_config
  ensure_clean_git

  cd "$SCRIPT_DIR"
  echo "Fetching production refs from ${PROD_GIT_URL} before rollback."
  git_https_in_repo fetch "$PROD_GIT_URL" --tags >/dev/null 2>&1 || true

  local resolved_ref
  if ! resolved_ref="$(git_in_repo rev-parse --verify "${target_ref}^{commit}" 2>/dev/null)"; then
    echo "Unknown rollback target: ${target_ref}" >&2
    exit 1
  fi
  echo "Checking out ${target_ref} (${resolved_ref}) for rollback."
  git_in_repo checkout --detach "$resolved_ref"

  PROD_SKIP_PULL=1
  run_prod --workspace "$WORKSPACE_DIR" --no-pull
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
    rollback)
      shift
      run_rollback "$@"
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
