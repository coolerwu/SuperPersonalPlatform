from pathlib import Path


RUN_SH = Path(__file__).resolve().parents[2] / "run.sh"


def read_run_sh() -> str:
    return RUN_SH.read_text(encoding="utf-8")


def test_prod_git_commands_use_command_scoped_safe_directory() -> None:
    script = read_run_sh()

    assert 'PROD_GIT_URL="https://github.com/coolerwu/SuperPersonalPlatform.git"' in script
    assert 'PROD_GIT_BRANCH="main"' in script
    assert "PROD_GIT_PULL_ATTEMPTS=3" in script
    assert 'git -c "safe.directory=${SCRIPT_DIR}" "$@"' in script
    assert 'git -c "safe.directory=${SCRIPT_DIR}" -c "http.version=HTTP/1.1" "$@"' in script
    assert "git_in_repo status --porcelain --untracked-files=all" in script
    assert "git_in_repo status --short" in script
    assert 'git_https_in_repo pull --ff-only "$PROD_GIT_URL" "$PROD_GIT_BRANCH"' in script
    assert "git pull failed; retrying" in script
    assert "git config --global --add safe.directory" not in script
    assert "GIT_SSH_COMMAND=" not in script
    assert "StrictHostKeyChecking" not in script


def test_prod_service_file_compare_avoids_sudo_when_unchanged() -> None:
    script = read_run_sh()

    assert '[[ -f "$SERVICE_PATH" ]] && cmp -s "$generated_service" "$SERVICE_PATH"' in script
    assert "systemd service unchanged; skipping install and daemon-reload" in script
    assert "sudo test -f" not in script
    assert "sudo cmp -s" not in script


def test_prod_enable_only_runs_when_service_file_changed() -> None:
    script = read_run_sh()

    assert "SERVICE_FILE_CHANGED=0" in script
    assert "SERVICE_FILE_CHANGED=1" in script
    assert 'if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" ]]; then' in script
    assert 'sudo systemctl enable "$SERVICE_NAME"' in script
    assert 'sudo systemctl restart "$SERVICE_NAME"' in script


def test_prod_service_uses_resolved_terminal_user() -> None:
    script = read_run_sh()

    assert "SUPER_PERSONAL_SERVICE_USER" in script
    assert 'if [[ -n "${SUDO_USER:-}" ]]; then' in script
    assert "id -un" in script
    assert 'service_user="$(resolve_service_user)"' in script
    assert 'service_group="$(resolve_service_group "$service_user")"' in script
    assert "User=${service_user}" in script
    assert "Group=${service_group}" in script
