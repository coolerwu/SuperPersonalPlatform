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


def test_python_dependency_install_retries_transient_pip_failures() -> None:
    script = read_run_sh()

    assert "PYTHON_DEPS_INSTALL_ATTEMPTS=3" in script
    assert 'pip" install --disable-pip-version-check --no-cache-dir --retries 5 --timeout 60 -e "$target"' in script
    assert "pip install failed; retrying" in script
    assert 'pip install failed after ${PYTHON_DEPS_INSTALL_ATTEMPTS} attempts.' in script


def test_python_dependency_install_skips_when_fingerprint_unchanged() -> None:
    script = read_run_sh()

    assert 'PYTHON_DEPS_STAMP_PREFIX=".super-personal-platform-python-deps"' in script
    assert "python_deps_fingerprint()" in script
    assert '"pyproject.toml",' in script
    assert 'digest.update(sys.version.encode("utf-8"))' in script
    assert 'stamp_path="$(python_deps_stamp_path "$name")"' in script
    assert 'if [[ "$current_fingerprint" == "$expected_fingerprint" ]]; then' in script
    assert 'echo "Python dependencies unchanged (${target}); skipping install."' in script
    assert 'printf \'%s\\n\' "$expected_fingerprint" >"$stamp_path"' in script
    assert 'install_python_deps ".[dev]" "dev"' in script
    assert 'install_python_deps "." "prod"' in script


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
    assert 'echo "Restarting ${SERVICE_NAME} because code_updated=${CODE_UPDATED:-0} service_file_changed=${SERVICE_FILE_CHANGED:-0}."' in script
    assert 'sudo systemctl restart "$SERVICE_NAME"' in script
    assert 'echo "Restart command completed for ${SERVICE_NAME}; fetching service status."' in script


def test_prod_service_keeps_update_process_alive_during_restart() -> None:
    script = read_run_sh()

    assert "KillMode=process" in script


def test_prod_preflights_restart_sudo_for_background_updates() -> None:
    script = read_run_sh()

    assert "can_restart_without_prompt()" in script
    assert 'sudo -n -l "$systemctl_path" restart "$SERVICE_NAME" >/dev/null 2>&1' in script
    run_prod_body = script.split("run_prod() {", 1)[1]
    assert '[[ "${CODE_UPDATED:-0}" == "1" ]] && ! can_restart_without_prompt && [[ ! -t 0 ]]' in run_prod_body
    assert "Code was pulled, but this no-TTY update cannot restart systemd without passwordless sudo." in run_prod_body
    assert 'if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" || "${CODE_UPDATED:-0}" == "1" ]]; then' in run_prod_body
    assert "No code or systemd unit changes; skipping systemctl enable/restart/status." in run_prod_body
    service_changed_block = run_prod_body.split('if [[ "${SERVICE_FILE_CHANGED:-0}" == "1" ]]; then', 1)[1]
    assert 'require_sudo "systemctl enable/restart/status"' in service_changed_block


def test_setup_sudo_installs_limited_restart_sudoers() -> None:
    script = read_run_sh()

    assert 'SUDOERS_PATH="/etc/sudoers.d/super-personal-platform"' in script
    assert "install_restart_sudoers()" in script
    assert "Managed by SuperPersonalPlatform run.sh." in script
    assert '${service_user} ALL=(root) NOPASSWD: ${systemctl_path} restart ${SERVICE_NAME}' in script
    assert '${service_user} ALL=(root) NOPASSWD: ${systemctl_path} status ${SERVICE_NAME} --no-pager' in script
    assert '${service_user} ALL=(root) NOPASSWD: ${systemctl_path} is-active ${SERVICE_NAME}' in script
    assert 'visudo -cf "$temp_file"' in script
    assert 'setup-sudo)' in script


def test_prod_detects_code_change_with_head_compare() -> None:
    script = read_run_sh()

    assert 'before_head="$(git_in_repo rev-parse HEAD)"' in script
    assert 'after_head="$(git_in_repo rev-parse HEAD)"' in script
    assert 'if [[ "$before_head" == "$after_head" ]]; then' in script
    assert "CODE_UPDATED=1" in script


def test_prod_service_uses_resolved_terminal_user() -> None:
    script = read_run_sh()

    assert "SUPER_PERSONAL_SERVICE_USER" in script
    assert 'if [[ -n "${SUDO_USER:-}" ]]; then' in script
    assert "id -un" in script
    assert 'service_user="$(resolve_service_user)"' in script
    assert 'service_group="$(resolve_service_group "$service_user")"' in script
    assert "User=${service_user}" in script
    assert "Group=${service_group}" in script
