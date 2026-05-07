from dataclasses import dataclass
import json
from pathlib import Path

from server.domain.agents import AgentConfigError, AgentDefinition, SKILL_ID_PATTERN


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
        name, _summary = self._metadata(skill_id, content)
        return AgentSkillContent(
            id=skill_id,
            name=name,
            content=content,
            truncated=truncated,
        )

    def toolbox(self, agent: AgentDefinition) -> "AgentSkillToolbox":
        return AgentSkillToolbox(self, agent)

    def _skill_path(self, agent: AgentDefinition, skill_id: str) -> Path:
        if not SKILL_ID_PATTERN.fullmatch(skill_id):
            raise AgentConfigError("Invalid skill id")
        namespace, stem = skill_id.split(":", 1)
        if namespace == "common":
            return self._skills_dir / "common" / f"{stem}.md"
        if namespace == "private":
            return self._skills_dir / "agents" / agent.id / f"{stem}.md"
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
