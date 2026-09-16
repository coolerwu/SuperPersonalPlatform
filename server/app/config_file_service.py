from pathlib import Path
from typing import Any

import yaml

from server.infrastructure.config import parse_settings


class InvalidConfigFileError(Exception):
    pass


class AgentPromptUpdateError(Exception):
    """Raised when an Agent system prompt cannot be replaced in config.yaml."""


MAX_SYSTEM_PROMPT_CHARS = 20000


class ConfigFileService:
    def __init__(self, workspace: Path) -> None:
        self.config_path = workspace / "config.yaml"

    def read_config(self) -> str:
        return self.config_path.read_text(encoding="utf-8")

    def write_config(self, content: str) -> None:
        try:
            raw = yaml.safe_load(content) or {}
            if not isinstance(raw, dict):
                raise InvalidConfigFileError("config.yaml 顶层必须是对象")
            parse_settings(raw)
        except InvalidConfigFileError:
            raise
        except Exception as exc:
            raise InvalidConfigFileError(str(exc)) from exc

        tmp_path = self.config_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(self.config_path)

    def update_agent_system_prompt(self, agent_id: str, system_prompt: str) -> dict[str, Any]:
        """Replace one Agent's system_prompt with a minimal edit, after full config validation."""
        normalized_id = str(agent_id or "").strip()
        if not normalized_id:
            raise AgentPromptUpdateError("缺少要修改的 Agent ID")
        normalized_prompt = _normalize_system_prompt(system_prompt)
        if not normalized_prompt:
            raise AgentPromptUpdateError("新的系统提示词不能为空")
        if len(normalized_prompt) > MAX_SYSTEM_PROMPT_CHARS:
            raise AgentPromptUpdateError(
                f"新的系统提示词超过 {MAX_SYSTEM_PROMPT_CHARS} 字符上限"
            )
        try:
            content = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AgentPromptUpdateError("config.yaml 不存在") from exc
        updated = _replace_agent_system_prompt(content, normalized_id, normalized_prompt)
        try:
            self.write_config(updated)
        except InvalidConfigFileError as exc:
            raise AgentPromptUpdateError(f"修改后的配置未通过校验：{exc}") from exc
        return {"agent_id": normalized_id, "length": len(normalized_prompt)}


def describe_system_prompt_update(config_path: Path, agent_id: str, args: Any) -> str:
    """Build the human-facing approval text comparing current and proposed system prompts.

    Never raises: this runs while a run is turning into an approval interrupt.
    """
    payload = args if isinstance(args, dict) else {}
    new_prompt = _normalize_system_prompt(payload.get("new_prompt") or "")
    reason = str(payload.get("reason") or "").strip()
    label = str(agent_id or "").strip() or "当前 Agent"
    current_prompt = ""
    try:
        settings = parse_settings(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
        agent = settings.agent_workspace.get_agent(label)
        current_prompt = agent.system_prompt
        label = f"{agent.name}（{agent.id}）"
    except Exception:  # noqa: BLE001 - approval text must survive a broken config
        current_prompt = "（读取当前 config.yaml 失败，请在 Web 端核对后决定）"
    lines = [
        f"Agent「{label}」申请修改本 Agent 的系统提示词；批准后将在下一次运行生效。",
    ]
    if reason:
        lines.append(f"理由：{reason}")
    lines.append("")
    lines.append("拟修改为（新）：")
    lines.append(new_prompt or "（未提供新的提示词文本）")
    lines.append("")
    lines.append("当前（旧）：")
    lines.append(current_prompt)
    return "\n".join(lines)


def _normalize_system_prompt(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _replace_agent_system_prompt(content: str, agent_id: str, system_prompt: str) -> str:
    try:
        root = yaml.compose(content)
    except yaml.YAMLError as exc:
        raise AgentPromptUpdateError(f"config.yaml 解析失败：{exc}") from exc
    item = _find_agent_item(root, agent_id)
    if item is None:
        raise AgentPromptUpdateError(f"config.yaml 中不存在 Agent「{agent_id}」")
    key_node, value_node = _find_mapping_entry(item, "system_prompt")
    if key_node is None or value_node is None:
        raise AgentPromptUpdateError(f"Agent「{agent_id}」没有可替换的 system_prompt")
    start = value_node.start_mark.index
    end = value_node.end_mark.index
    original = content[start:end]
    # Keep a same-line trailing comment attached to the new block indicator line
    # instead of letting it fall inside the replaced scalar content.
    comment = ""
    if not original.endswith("\n"):
        line_end = content.find("\n", end)
        if line_end == -1:
            line_end = len(content)
        tail = content[end:line_end]
        if tail.strip():
            if not tail.lstrip().startswith("#"):
                raise AgentPromptUpdateError(
                    f"Agent「{agent_id}」的 system_prompt 行尾内容无法安全替换"
                )
            comment = tail
            end = line_end
    replacement = _literal_block(
        system_prompt,
        indent=key_node.start_mark.column + 2,
        comment=comment,
    )
    if not original.endswith("\n") and content[end : end + 1] == "\n":
        end += 1
    return content[:start] + replacement + content[end:]


def _literal_block(text: str, *, indent: int, comment: str = "") -> str:
    pad = " " * indent
    lines = [f"{pad}{line}" if line else "" for line in text.split("\n")]
    return f"|-{comment}\n" + "\n".join(lines) + "\n"


def _find_agent_item(root: Any, agent_id: str) -> Any:
    if not isinstance(root, yaml.MappingNode):
        return None
    _, agents_node = _find_mapping_entry(root, "agents")
    if not isinstance(agents_node, yaml.MappingNode):
        return None
    _, definitions_node = _find_mapping_entry(agents_node, "definitions")
    if not isinstance(definitions_node, yaml.SequenceNode):
        return None
    for item in definitions_node.value:
        if not isinstance(item, yaml.MappingNode):
            continue
        _, id_node = _find_mapping_entry(item, "id")
        if isinstance(id_node, yaml.ScalarNode) and str(id_node.value or "").strip() == agent_id:
            return item
    return None


def _find_mapping_entry(node: Any, key: str) -> tuple[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        return (None, None)
    for candidate_key, candidate_value in node.value:
        if isinstance(candidate_key, yaml.ScalarNode) and str(candidate_key.value) == key:
            return (candidate_key, candidate_value)
    return (None, None)
