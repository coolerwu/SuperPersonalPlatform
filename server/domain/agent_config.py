import math
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
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    price_currency: str = "USD"

    def __post_init__(self) -> None:
        for price in (self.input_price_per_million, self.output_price_per_million):
            if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0):
                raise AgentConfigError("model prices must be finite non-negative numbers")
        if self.price_currency not in {"USD", "CNY"}:
            raise AgentConfigError("price_currency must be USD or CNY")
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
class AgentWebDAVDirectory:
    path: str = "/"
    permission: str = "write"
    description: str = ""

    def __post_init__(self) -> None:
        from pathlib import PurePosixPath
        if not self.path.startswith("/") or any(part in {".", "..", "~"} for part in self.path.split("/")) or "\\" in self.path or "\0" in self.path:
            raise AgentConfigError("agents.definitions[].webdav.directories[].path must be a safe absolute mapping path")
        object.__setattr__(self, "path", "/" + "/".join(PurePosixPath(self.path).parts[1:]))
        if self.permission not in {"read", "write"}:
            raise AgentConfigError("agents.definitions[].webdav.directories[].permission must be read or write")


@dataclass(frozen=True)
class AgentWebDAVConfig:
    enabled: bool = False
    directories: tuple[AgentWebDAVDirectory, ...] = ()

    def __post_init__(self) -> None:
        paths = [directory.path for directory in self.directories]
        if len(paths) != len(set(paths)):
            raise AgentConfigError("WebDAV directories must not contain duplicate paths")


@dataclass(frozen=True)
class DeepAgentOptions:
    max_iterations: int = 60
    todo_list: bool = True
    tools: tuple[str, ...] = ()

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
    webdav: AgentWebDAVConfig = AgentWebDAVConfig()

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
