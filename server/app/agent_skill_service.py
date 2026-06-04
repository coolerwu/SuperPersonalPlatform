from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from server.domain.agents import AgentConfigError, AgentDefinition, SKILL_ID_PATTERN, ToolAccessDefinition


MAX_SKILL_CONTENT_CHARS = 20_000
SUMMARY_LINES = 3


@dataclass(frozen=True)
class AgentSkillSummary:
    id: str
    name: str
    summary: str


@dataclass(frozen=True)
class AgentSkillContent:
    id: str
    name: str
    content: str
    truncated: bool
    tools: ToolAccessDefinition = ToolAccessDefinition()


class AgentSkillService:
    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)
        self._skills_dir = self._workspace / "skills"

    def list_skills(self, agent: AgentDefinition) -> tuple[AgentSkillSummary, ...]:
        summaries: list[AgentSkillSummary] = []
        for skill_id in agent.skill_ids:
            path = self._skill_path(agent, skill_id)
            if not path.exists() or not path.is_file():
                continue
            content = self._read_text(path)
            name, summary = self._metadata(skill_id, content)
            summaries.append(AgentSkillSummary(id=skill_id, name=name, summary=summary))
        return tuple(summaries)

    def read_skill(self, agent: AgentDefinition, skill_id: str) -> AgentSkillContent:
        if not SKILL_ID_PATTERN.fullmatch(skill_id):
            raise AgentConfigError("Invalid skill id")
        if skill_id not in agent.skill_ids:
            raise AgentConfigError("Skill is not enabled for this Agent")
        path = self._skill_path(agent, skill_id)
        if not path.exists() or not path.is_file():
            raise AgentConfigError("Skill does not exist")
        content = self._read_text(path)
        truncated = len(content) > MAX_SKILL_CONTENT_CHARS
        if truncated:
            content = content[:MAX_SKILL_CONTENT_CHARS] + "\n\n[skill content truncated]"
        metadata, body = self._split_frontmatter(content)
        name, _summary = self._metadata(skill_id, body)
        return AgentSkillContent(
            id=skill_id,
            name=str(metadata.get("name") or name or skill_id).strip(),
            content=body,
            truncated=truncated,
            tools=self._parse_tool_access(metadata.get("tools")),
        )

    def read_workspace_skill(self, skill_id: str, agent_id: str | None = None) -> AgentSkillContent:
        path = self._workspace_skill_path(skill_id, agent_id)
        if not path.exists() or not path.is_file():
            return AgentSkillContent(id=skill_id, name=skill_id, content="", truncated=False)
        content = self._read_text(path)
        truncated = len(content) > MAX_SKILL_CONTENT_CHARS
        if truncated:
            content = content[:MAX_SKILL_CONTENT_CHARS] + "\n\n[skill content truncated]"
        metadata, body = self._split_frontmatter(content)
        name, _summary = self._metadata(skill_id, body)
        return AgentSkillContent(
            id=skill_id,
            name=str(metadata.get("name") or name or skill_id).strip(),
            content=body,
            truncated=truncated,
            tools=self._parse_tool_access(metadata.get("tools")),
        )

    def write_workspace_skill(
        self,
        skill_id: str,
        content: str,
        agent_id: str | None = None,
        *,
        name: str = "",
        tools: ToolAccessDefinition | None = None,
    ) -> Path:
        if len(content) > MAX_SKILL_CONTENT_CHARS:
            raise AgentConfigError("Skill content is too large")
        path = self._workspace_skill_path(skill_id, agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata: dict[str, Any] = {}
        if tools is not None:
            metadata["tools"] = {
                "profile": tools.profile,
                "allow": list(tools.allow),
                "deny": list(tools.deny),
            }
        path.write_text(self._join_frontmatter(metadata, content), encoding="utf-8")
        return path

    def toolbox(self, agent: AgentDefinition) -> "AgentSkillToolbox":
        return AgentSkillToolbox(self, agent)

    def _skill_path(self, agent: AgentDefinition, skill_id: str) -> Path:
        if not SKILL_ID_PATTERN.fullmatch(skill_id):
            raise AgentConfigError("Invalid skill id")
        namespace, stem = skill_id.split(":", 1)
        if namespace == "common":
            directory_path = self._skills_dir / "common" / stem / "SKILL.md"
            if directory_path.exists():
                return directory_path
            return self._skills_dir / "common" / f"{stem}.md"
        if namespace == "private":
            directory_path = self._skills_dir / "agents" / agent.id / stem / "SKILL.md"
            if directory_path.exists():
                return directory_path
            return self._skills_dir / "agents" / agent.id / f"{stem}.md"
        raise AgentConfigError("Invalid skill namespace")

    def _workspace_skill_path(self, skill_id: str, agent_id: str | None = None) -> Path:
        if not SKILL_ID_PATTERN.fullmatch(skill_id):
            raise AgentConfigError("Invalid skill id")
        namespace, stem = skill_id.split(":", 1)
        if namespace == "common":
            directory_path = self._skills_dir / "common" / stem / "SKILL.md"
            legacy_path = self._skills_dir / "common" / f"{stem}.md"
            if directory_path.exists() or not legacy_path.exists():
                return directory_path
            return legacy_path
        if namespace == "private":
            clean_agent_id = str(agent_id or "").strip()
            if not clean_agent_id:
                raise AgentConfigError("agent_id is required for private skills")
            directory_path = self._skills_dir / "agents" / clean_agent_id / stem / "SKILL.md"
            legacy_path = self._skills_dir / "agents" / clean_agent_id / f"{stem}.md"
            if directory_path.exists() or not legacy_path.exists():
                return directory_path
            return legacy_path
        raise AgentConfigError("Invalid skill namespace")

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def _metadata(self, skill_id: str, content: str) -> tuple[str, str]:
        title = skill_id
        body_lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("# ") and title == skill_id:
                title = stripped[2:].strip() or skill_id
                continue
            if len(body_lines) < SUMMARY_LINES:
                body_lines.append(stripped)
        return title, " ".join(body_lines)

    def _split_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---\n"):
            return {}, content
        marker = "\n---\n"
        end_index = content.find(marker, 4)
        if end_index == -1:
            return {}, content
        raw_metadata = content[4:end_index]
        body = content[end_index + len(marker):]
        metadata = yaml.safe_load(raw_metadata) or {}
        if not isinstance(metadata, dict):
            raise AgentConfigError("Skill frontmatter must be an object")
        return metadata, body

    def _join_frontmatter(self, metadata: dict[str, Any], content: str) -> str:
        frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{frontmatter}\n---\n{content.lstrip(chr(10))}"

    def _parse_tool_access(self, raw: Any) -> ToolAccessDefinition:
        if raw is None:
            return ToolAccessDefinition()
        if not isinstance(raw, dict):
            raise AgentConfigError("Skill frontmatter tools must be an object")
        allow_raw = raw.get("allow") or []
        deny_raw = raw.get("deny") or []
        if not isinstance(allow_raw, list):
            raise AgentConfigError("Skill frontmatter tools.allow must be a list")
        if not isinstance(deny_raw, list):
            raise AgentConfigError("Skill frontmatter tools.deny must be a list")
        return ToolAccessDefinition(
            profile=str(raw.get("profile") or "default").strip() or "default",
            allow=tuple(str(tool).strip() for tool in allow_raw if str(tool).strip()),
            deny=tuple(str(tool).strip() for tool in deny_raw if str(tool).strip()),
        )


class AgentSkillToolbox:
    def __init__(self, service: AgentSkillService, agent: AgentDefinition) -> None:
        self._service = service
        self._agent = agent

    async def list_skill(self) -> str:
        return json.dumps(
            {
                "skills": [
                    {"id": skill.id, "name": skill.name, "summary": skill.summary}
                    for skill in self._service.list_skills(self._agent)
                ]
            },
            ensure_ascii=False,
        )

    async def read_skill(self, id: str) -> str:
        try:
            skill = self._service.read_skill(self._agent, id)
        except AgentConfigError as exc:
            return json.dumps(
                {"error": str(exc), "id": id},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "id": skill.id,
                "name": skill.name,
                "content": skill.content,
                "truncated": skill.truncated,
            },
            ensure_ascii=False,
        )
