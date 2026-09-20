import json
from pathlib import Path
from typing import Any

import yaml

from server.infrastructure.config import parse_settings
from server.domain.system_prompt import resolve_system_prompt, system_prompt_source


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
        if not isinstance(system_prompt, str):
            raise AgentPromptUpdateError("必须提供字符串 new_prompt；空字符串表示恢复平台默认规则")
        normalized_prompt = _normalize_system_prompt(system_prompt)
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

    def read_agent_system_prompt(self, agent_id: str) -> dict[str, Any]:
        """Return configured text and the effective base prompt for the next Run."""
        normalized_id = str(agent_id or "").strip()
        if not normalized_id:
            raise AgentPromptUpdateError("缺少要读取的 Agent ID")
        try:
            content = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AgentPromptUpdateError("config.yaml 不存在") from exc
        except OSError as exc:
            raise AgentPromptUpdateError(f"读取 config.yaml 失败：{exc}") from exc
        try:
            raw = yaml.safe_load(content) or {}
            settings = parse_settings(raw)
        except Exception as exc:  # noqa: BLE001
            raise AgentPromptUpdateError(f"config.yaml 未通过校验：{exc}") from exc
        if not any(agent.id == normalized_id for agent in settings.agent_workspace.agents):
            raise AgentPromptUpdateError(f"config.yaml 中不存在 Agent「{normalized_id}」")
        agent = settings.agent_workspace.get_agent(normalized_id)
        configured = str(next(
            item for item in raw["agents"]["definitions"]
            if str(item.get("id", "")).strip() == agent.id
        ).get("system_prompt") or "")
        return {
            "agent_id": agent.id,
            "name": agent.name,
            "system_prompt": configured,
            "length": len(configured),
            "effective_system_prompt": resolve_system_prompt(agent.system_prompt),
            "source": system_prompt_source(agent.system_prompt),
        }


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
        current_prompt = agent.system_prompt if agent.system_prompt.strip() else "（平台默认规则）\n" + resolve_system_prompt(agent.system_prompt)
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
    if not isinstance(payload.get("new_prompt"), str):
        lines.append("（未提供新的提示词文本）")
    else:
        lines.append(new_prompt or "（恢复平台默认规则）\n" + resolve_system_prompt(new_prompt))
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
        if item.flow_style:
            # Flow mappings cannot contain literal blocks; insert a JSON-quoted YAML scalar.
            end = item.end_mark.index - 1
            return content[:end] + ", system_prompt: " + json.dumps(system_prompt, ensure_ascii=False) + content[end:]
        first_key = item.value[0][0]
        line_end = content.find("\n", first_key.start_mark.index)
        if line_end == -1:
            line_end = len(content)
            content += "\n"
        indent = first_key.start_mark.column
        insertion = " " * indent + "system_prompt: " + _literal_block(system_prompt, indent=indent + 2)
        return content[:line_end + 1] + insertion + content[line_end + 1:]
    start = value_node.start_mark.index
    end = value_node.end_mark.index
    if item.flow_style:
        return content[:start] + json.dumps(system_prompt, ensure_ascii=False) + content[end:]
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
