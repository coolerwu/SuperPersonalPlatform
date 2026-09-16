import pytest
import yaml

from server.app.config_file_service import (
    AgentPromptUpdateError,
    ConfigFileService,
    MAX_SYSTEM_PROMPT_CHARS,
)
from server.infrastructure.config import parse_settings


CONFIG = """\
# 顶部注释
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
agents:
  default_agent_id: assistant
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.  # 保留行尾注释
      model_id: default
      skill_ids: []
    - id: advisor
      name: 顾问
      system_prompt: '多行

        引用写法'
      model_id: default
    - id: third
      name: 第三
      system_prompt: |
        第一行
        第二行
      model_id: default
"""


def _service(tmp_path, content: str = CONFIG) -> ConfigFileService:
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    return ConfigFileService(tmp_path)


def _prompts(tmp_path) -> dict[str, str]:
    settings = parse_settings(yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8")))
    return {agent.id: agent.system_prompt for agent in settings.agent_workspace.agents}


def test_update_agent_system_prompt_edits_only_target_scalar(tmp_path) -> None:
    service = _service(tmp_path)
    original = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    advisor_block = original[original.index("    - id: advisor") : original.index("    - id: third")]
    third_block = original[original.index("    - id: third") :]

    result = service.update_agent_system_prompt("assistant", "你是新助理。\n第二行")

    updated = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert result == {"agent_id": "assistant", "length": len("你是新助理。\n第二行")}
    assert updated.startswith("# 顶部注释\n")
    assert "      system_prompt: |-  # 保留行尾注释\n        你是新助理。\n        第二行\n" in updated
    assert advisor_block in updated
    assert third_block in updated
    assert not (tmp_path / "config.yaml.tmp").exists()
    assert _prompts(tmp_path) == {
        "assistant": "你是新助理。\n第二行",
        "advisor": "多行\n引用写法",
        "third": "第一行\n第二行",
    }


@pytest.mark.parametrize(
    ("agent_id", "prompt"),
    [
        ("advisor", "单行人格"),
        ("advisor", "第一行\n\n第三行"),
        ("third", "块标量替换"),
        ("third", "多行\n  带缩进\n\n结尾"),
        ("assistant", "带 trailing 空格以外的文本"),
    ],
)
def test_update_agent_system_prompt_round_trips_scalar_styles(tmp_path, agent_id, prompt) -> None:
    service = _service(tmp_path)

    service.update_agent_system_prompt(agent_id, prompt)

    assert _prompts(tmp_path)[agent_id] == prompt


def test_update_agent_system_prompt_normalizes_line_endings_and_padding(tmp_path) -> None:
    service = _service(tmp_path)

    service.update_agent_system_prompt("assistant", "  第一行\r\n第二行  ")

    assert _prompts(tmp_path)["assistant"] == "第一行\n第二行"


def test_update_agent_system_prompt_rejects_unknown_agent_without_writing(tmp_path) -> None:
    service = _service(tmp_path)
    before = (tmp_path / "config.yaml").read_bytes()

    with pytest.raises(AgentPromptUpdateError):
        service.update_agent_system_prompt("missing", "new prompt")

    assert (tmp_path / "config.yaml").read_bytes() == before


@pytest.mark.parametrize("prompt", ["", "   ", "\n\n"])
def test_update_agent_system_prompt_rejects_empty_prompt(tmp_path, prompt) -> None:
    service = _service(tmp_path)
    before = (tmp_path / "config.yaml").read_bytes()

    with pytest.raises(AgentPromptUpdateError):
        service.update_agent_system_prompt("assistant", prompt)

    assert (tmp_path / "config.yaml").read_bytes() == before


def test_update_agent_system_prompt_rejects_over_long_prompt(tmp_path) -> None:
    service = _service(tmp_path)
    before = (tmp_path / "config.yaml").read_bytes()

    with pytest.raises(AgentPromptUpdateError):
        service.update_agent_system_prompt("assistant", "x" * (MAX_SYSTEM_PROMPT_CHARS + 1))

    assert (tmp_path / "config.yaml").read_bytes() == before


def test_update_agent_system_prompt_keeps_file_when_config_is_invalid(tmp_path) -> None:
    invalid = CONFIG.replace("      model_id: default\n", "      model_id: missing-model\n", 1)
    service = _service(tmp_path, invalid)
    before = (tmp_path / "config.yaml").read_bytes()

    with pytest.raises(AgentPromptUpdateError):
        service.update_agent_system_prompt("assistant", "新人格")

    assert (tmp_path / "config.yaml").read_bytes() == before
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_update_agent_system_prompt_reports_broken_yaml(tmp_path) -> None:
    service = _service(tmp_path, "auth:\n  token: [unclosed\n")
    before = (tmp_path / "config.yaml").read_bytes()

    with pytest.raises(AgentPromptUpdateError):
        service.update_agent_system_prompt("assistant", "新人格")

    assert (tmp_path / "config.yaml").read_bytes() == before
