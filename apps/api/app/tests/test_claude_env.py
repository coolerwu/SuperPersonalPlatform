import os
import stat

from app.services import claude_env
from app.services.claude_env import (
    build_claude_env,
    claude_paths,
    ensure_claude_dirs,
    install_claude_process_env,
    workspace_for,
)
from app.services.settings_store import AgentSection, ProviderSection

DEEPSEEK_PROVIDER = ProviderSection(
    base_url="https://api.deepseek.com/anthropic",
    model="deepseek-flash[1m]",
    api_key="sk-test",
)


def _agent(provider: ProviderSection | None = None) -> AgentSection:
    return AgentSection(
        id="default",
        name="Assistant",
        provider=provider or DEEPSEEK_PROVIDER,
    )


def test_claude_paths_are_inside_home(tmp_path):
    paths = claude_paths(tmp_path)
    assert paths["config"] == tmp_path / "claude"
    assert paths["tmp"] == tmp_path / "tmp"
    assert paths["workspaces"] == tmp_path / "workspaces"


def test_claude_paths_use_platform_workspaces_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        claude_env.app_settings, "app_workspaces_dir", tmp_path / "custom-workspaces"
    )
    paths = claude_paths()
    assert paths["workspaces"] == tmp_path / "custom-workspaces"


def test_build_env_targets_deepseek(tmp_path):
    env = build_claude_env(_agent(), home=tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert env["CLAUDE_CODE_PROJECT_DIR_NAME"] == "default"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    assert "HOME" not in env


def test_build_env_full_dict(tmp_path):
    env = build_claude_env(_agent(), home=tmp_path)
    assert env == {
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "CLAUDE_CODE_PROJECT_DIR_NAME": "default",
        "TMPDIR": str(tmp_path / "tmp"),
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_MODEL": "deepseek-flash[1m]",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash[1m]",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
        "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
        "CLAUDE_CODE_EFFORT_LEVEL": "max",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "786432",
    }
    assert len(env) == 11


def test_build_env_without_long_context_suffix(tmp_path):
    provider = ProviderSection(
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-flash",
        api_key="sk-test",
    )
    env = build_claude_env(_agent(provider), home=tmp_path)
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "deepseek-flash"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-flash"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-flash"


def test_build_env_drops_blank_provider_values(tmp_path):
    provider = ProviderSection(base_url="  ", model="   ", api_key="\t")
    env = build_claude_env(_agent(provider), home=tmp_path)
    assert not any(key.startswith("ANTHROPIC_") for key in env)
    assert set(env) == {
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_PROJECT_DIR_NAME",
        "TMPDIR",
        "CLAUDE_CODE_EFFORT_LEVEL",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
    }


def test_workspace_for_uses_agent_id(tmp_path):
    assert workspace_for("agent-1", home=tmp_path) == (
        tmp_path / "workspaces" / "agent-1"
    )


def test_ensure_claude_dirs_creates_root_dirs(tmp_path):
    ensure_claude_dirs(tmp_path)
    assert (tmp_path / "claude").is_dir()
    assert (tmp_path / "tmp").is_dir()
    assert (tmp_path / "workspaces").is_dir()
    assert list((tmp_path / "workspaces").iterdir()) == []


def test_ensure_claude_dirs_config_is_private(tmp_path):
    ensure_claude_dirs(tmp_path)
    mode = stat.S_IMODE((tmp_path / "claude").stat().st_mode)
    assert mode == 0o700


def test_ensure_claude_dirs_tmp_and_workspaces_are_private(tmp_path):
    ensure_claude_dirs(tmp_path)
    assert stat.S_IMODE((tmp_path / "tmp").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "workspaces").stat().st_mode) == 0o700


def test_install_claude_process_env_overrides_existing_value(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere/claude")
    config_dir = install_claude_process_env(tmp_path)
    assert config_dir == tmp_path / "claude"
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")


def test_install_claude_process_env_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere/claude")
    first = install_claude_process_env(tmp_path)
    second = install_claude_process_env(tmp_path)
    assert first == second == tmp_path / "claude"
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
