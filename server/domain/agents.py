from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AgentConfigError(ValueError):
    pass


class ModelProvider(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    provider: ModelProvider = ModelProvider.OPENAI_COMPATIBLE
    temperature: float | None = None
    supports_images: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise AgentConfigError("llm.models[].id is required")
        if not self.name.strip():
            raise AgentConfigError(f"llm.models[{self.id}].name is required")
        if self.provider is ModelProvider.OPENAI_COMPATIBLE and not self.base_url.strip():
            raise AgentConfigError(f"llm.models[{self.id}].base_url is required")
        if not self.api_key.strip():
            raise AgentConfigError(f"llm.models[{self.id}].api_key is required")
        if not self.model.strip():
            raise AgentConfigError(f"llm.models[{self.id}].model is required")


@dataclass(frozen=True)
class DeepAgentOptions:
    max_iterations: int = 60
    name: str = ""
    debug: bool = False
    use_longterm_memory: bool = False
    tools: tuple[str, ...] = ()
    interrupt_on: tuple[str, ...] = ()
    middleware: tuple[str, ...] = ()
    subagents: tuple[dict[str, Any], ...] = ()
    response_format: str = ""
    context_schema: str = ""
    checkpointer: bool = False
    store: str = ""
    cache: str = ""

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise AgentConfigError("agents.definitions[].deepagent.max_iterations must be greater than zero")


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    system_prompt: str
    model_id: str | None = None
    context_ids: tuple[str, ...] = ()
    deepagent: DeepAgentOptions = DeepAgentOptions()

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise AgentConfigError("agents.definitions[].id is required")
        if not self.name.strip():
            raise AgentConfigError(f"agents.definitions[{self.id}].name is required")
        if not self.system_prompt.strip():
            raise AgentConfigError(f"agents.definitions[{self.id}].system_prompt is required")


@dataclass(frozen=True)
class AgentWorkspaceDefinition:
    models: tuple[ModelDefinition, ...]
    default_model_id: str
    agents: tuple[AgentDefinition, ...]

    def __post_init__(self) -> None:
        model_ids = {model.id for model in self.models}
        agent_ids = {agent.id for agent in self.agents}
        if len(model_ids) != len(self.models):
            raise AgentConfigError("llm.models[].id must be unique")
        if len(agent_ids) != len(self.agents):
            raise AgentConfigError("agents.definitions[].id must be unique")
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
