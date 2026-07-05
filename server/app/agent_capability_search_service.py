from dataclasses import dataclass
import re
from typing import Iterable

from server.app.agent_tool_service import AgentToolRegistry
from server.domain.agents import AgentConfigError, AgentPlatformDefinition


_WORD_RE = re.compile(r"[A-Za-z0-9_:-]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_PUNCT_RE = re.compile(r"[^\w:\-\u4e00-\u9fff]+", re.UNICODE)

@dataclass(frozen=True)
class AgentCapabilitySearchResult:
    type: str
    id: str
    name: str
    description: str
    score: float
    matched_terms: tuple[str, ...]
    discoverable: bool
    loadable: bool
    callable: bool
    required_skills: list[str] | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
            "discoverable": self.discoverable,
            "loadable": self.loadable,
            "callable": self.callable,
            "required_skills": list(self.required_skills or []),
        }


class AgentCapabilitySearchService:
    def __init__(self, tool_registry: AgentToolRegistry) -> None:
        self._tool_registry = tool_registry

    def search(
        self,
        *,
        platform: AgentPlatformDefinition,
        query: str,
        types: tuple[str, ...] = ("skill", "tool"),
        agent_id: str = "",
        limit: int = 20,
    ) -> tuple[AgentCapabilitySearchResult, ...]:
        normalized_types = tuple(dict.fromkeys(str(item).strip().lower() for item in types if str(item).strip()))
        unknown_types = set(normalized_types) - {"skill", "tool"}
        if unknown_types:
            raise AgentConfigError(f"Unsupported search type: {sorted(unknown_types)[0]}")
        if limit <= 0:
            return ()

        query_terms = _tokenize(query)
        if not query_terms:
            return ()

        agent = None
        bound_skill_ids: set[str] = set()
        callable_tools: set[str] = set()
        if agent_id.strip():
            agent = platform.get_agent(agent_id.strip())
            bound_skill_ids = set(agent.skill_ids)
            callable_tools = set(self._tool_registry.resolve_tools(agent, platform.skill_definitions))

        results: list[AgentCapabilitySearchResult] = []
        if "skill" in normalized_types:
            for skill in platform.skill_definitions:
                score, matched = _score_item(
                    query_terms,
                    identifier=skill.id,
                    name=skill.name or skill.id,
                    description=" ".join(skill.tools.allow),
                )
                if score <= 0:
                    continue
                loadable = skill.id in bound_skill_ids if agent is not None else False
                results.append(
                    AgentCapabilitySearchResult(
                        type="skill",
                        id=skill.id,
                        name=skill.name or skill.id,
                        description="Skill 工作流" if not skill.tools.allow else f"允许工具: {', '.join(skill.tools.allow)}",
                        score=score + (16 if loadable else 0) + 8,
                        matched_terms=matched,
                        discoverable=True,
                        loadable=loadable,
                        callable=False,
                    )
                )

        if "tool" in normalized_types:
            required_by_tool = self._required_skills_by_tool(platform)
            for tool in self._tool_registry.public_definitions():
                tool_id = str(tool["name"])
                score, matched = _score_item(
                    query_terms,
                    identifier=tool_id,
                    name=str(tool["display_name"]),
                    description=str(tool["description"]),
                    extra_terms=tuple(str(scene) for scene in tool.get("support_scene", [])),
                )
                if score <= 0:
                    continue
                is_callable = tool_id in callable_tools
                results.append(
                    AgentCapabilitySearchResult(
                        type="tool",
                        id=tool_id,
                        name=str(tool["display_name"]),
                        description=str(tool["description"]),
                        score=score + (8 if is_callable else 0),
                        matched_terms=matched,
                        discoverable=True,
                        loadable=is_callable,
                        callable=is_callable,
                        required_skills=list(required_by_tool.get(tool_id, ())),
                    )
                )

        return tuple(
            sorted(
                results,
                key=lambda item: (-item.score, item.type != "skill", item.id),
            )[:limit]
        )

    def _required_skills_by_tool(self, platform: AgentPlatformDefinition) -> dict[str, tuple[str, ...]]:
        pairs: dict[str, list[str]] = {}
        for skill in platform.skill_definitions:
            for tool_name in skill.tools.allow:
                pairs.setdefault(tool_name, []).append(skill.id)
        return {tool_name: tuple(skill_ids) for tool_name, skill_ids in pairs.items()}


def _score_item(
    query_terms: tuple[str, ...],
    *,
    identifier: str,
    name: str,
    description: str,
    extra_terms: Iterable[str] = (),
) -> tuple[float, tuple[str, ...]]:
    item_terms = set(_tokenize(" ".join((identifier, name, description, " ".join(extra_terms)))))
    normalized_id = identifier.lower()
    normalized_name = name.lower()
    normalized_description = description.lower()
    matched: list[str] = []
    score = 0.0
    for term in query_terms:
        if term in item_terms or term in normalized_id or term in normalized_name or term in normalized_description:
            matched.append(term)
            if term in normalized_id:
                score += 4
            if term in normalized_name:
                score += 6
            if term in normalized_description:
                score += 3
            if term in item_terms:
                score += 1
    return score, tuple(dict.fromkeys(matched))


def _tokenize(text: str) -> tuple[str, ...]:
    normalized = _PUNCT_RE.sub(" ", text.lower())
    tokens: list[str] = []
    try:
        import jieba  # type: ignore

        tokens.extend(str(token).strip().lower() for token in jieba.cut(normalized) if str(token).strip())
    except Exception:
        pass

    tokens.extend(_WORD_RE.findall(normalized))
    for chunk in _CJK_RE.findall(normalized):
        tokens.append(chunk)
        if len(chunk) > 1:
            tokens.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return tuple(dict.fromkeys(token for token in tokens if token))
