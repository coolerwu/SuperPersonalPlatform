from pathlib import Path

from app.services.claude_env import build_claude_env, claude_paths
from app.services.settings_store import AgentSection, ProviderSection


def test_claude_paths_are_inside_home(tmp_path):
    paths = claude_paths(tmp_path)
    assert paths["config"] == tmp_path / "claude"
    assert paths["tmp"] == tmp_path / "tmp"


def test_build_env_targets_deepseek(tmp_path):
    agent = AgentSection(
        id="default",
        name="Assistant",
        provider=ProviderSection(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash[1m]",
            api_key="sk-test",
        ),
    )
    env = build_claude_env(agent, home=tmp_path)
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert env["CLAUDE_CODE_PROJECT_DIR_NAME"] == "default"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "deepseek-flash[1m]"
    assert "HOME" not in env
