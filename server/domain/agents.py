from dataclasses import dataclass
from enum import StrEnum
import re


class AgentConfigError(ValueError):
    pass


SUPPORTED_PROVIDERS = {"openai_compatible", "anthropic"}
SKILL_ID_PATTERN = re.compile(r"^(common|private):[A-Za-z0-9_-]+$")


class HarnessMode(StrEnum):
    PROMPT = "prompt"
    AGENT = "agent"


@dataclass(frozen=True)
class ToolAccessDefinition:
    allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    provider: str = "openai_compatible"
    temperature: float | None = None
    supports_images: bool = False
    mode: HarnessMode = HarnessMode.PROMPT

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise AgentConfigError("llm.models[].id is required")
        if not self.name.strip():
            raise AgentConfigError(f"llm.models[{self.id}].name is required")
        if self.provider not in SUPPORTED_PROVIDERS:
            raise AgentConfigError(
                f"llm.models[{self.id}].provider must be one of {sorted(SUPPORTED_PROVIDERS)}"
            )
        if self.provider != "anthropic" and not self.base_url.strip():
            raise AgentConfigError(f"llm.models[{self.id}].base_url is required for non-Anthropic providers")
        if not self.api_key.strip():
            raise AgentConfigError(f"llm.models[{self.id}].api_key is required")
        if not self.model.strip():
            raise AgentConfigError(f"llm.models[{self.id}].model is required")
        if not isinstance(self.mode, HarnessMode):
            raise AgentConfigError(
                f"llm.models[{self.id}].mode must be one of "
                f"{[mode.value for mode in HarnessMode]}"
            )


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    system_prompt: str
    model_id: str | None = None
    skill_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise AgentConfigError("agents.definitions[].id is required")
        if not self.name.strip():
            raise AgentConfigError(f"agents.definitions[{self.id}].name is required")
        if not self.system_prompt.strip():
            raise AgentConfigError(f"agents.definitions[{self.id}].system_prompt is required")
        for skill_id in self.skill_ids:
            if not SKILL_ID_PATTERN.fullmatch(skill_id):
                raise AgentConfigError(
                    f"agents.definitions[{self.id}].skill_ids contains invalid skill id"
                )


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str = ""
    tools: ToolAccessDefinition = ToolAccessDefinition()

    def __post_init__(self) -> None:
        if not SKILL_ID_PATTERN.fullmatch(self.id):
            raise AgentConfigError("skills.definitions[].id contains invalid skill id")


@dataclass(frozen=True)
class AgentPlatformDefinition:
    models: tuple[ModelDefinition, ...]
    default_model_id: str
    agents: tuple[AgentDefinition, ...]
    skill_definitions: tuple[SkillDefinition, ...] = ()

    def __post_init__(self) -> None:
        model_ids = {model.id for model in self.models}
        agent_ids = {agent.id for agent in self.agents}
        skill_ids = {skill.id for skill in self.skill_definitions}
        if len(model_ids) != len(self.models):
            raise AgentConfigError("llm.models[].id must be unique")
        if len(agent_ids) != len(self.agents):
            raise AgentConfigError("agents.definitions[].id must be unique")
        if len(skill_ids) != len(self.skill_definitions):
            raise AgentConfigError("skills.definitions[].id must be unique")
        if self.models and self.default_model_id not in model_ids:
            raise AgentConfigError("llm.default_model_id must reference an existing model")
        for agent in self.agents:
            if agent.model_id and agent.model_id not in model_ids:
                raise AgentConfigError(
                    f"agents.definitions[{agent.id}].model_id must reference an existing model"
                )

    def get_agent(self, agent_id: str) -> AgentDefinition:
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        raise AgentConfigError("Agent does not exist")

    def get_model(self, model_id: str) -> ModelDefinition:
        for model in self.models:
            if model.id == model_id:
                return model
        raise AgentConfigError("Model does not exist")
