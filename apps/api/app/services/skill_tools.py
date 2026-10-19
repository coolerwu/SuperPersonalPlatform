from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain_core.tools import BaseTool

# These tools are always exposed by deepagents; a skill tool with the same name
# would be treated as already visible and never gated.
RESERVED_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute", "task"}
)


class SkillToolRegistry:
    """Tools a skill can unlock through `metadata.include_tools`."""

    def __init__(self, tools: Iterable[BaseTool] = ()) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        name = getattr(tool, "name", "")
        if not isinstance(name, str) or not name or any(char.isspace() for char in name):
            raise ValueError(f"invalid skill tool name: {name!r}")
        if name in RESERVED_TOOL_NAMES:
            raise ValueError(f"skill tool name {name!r} is reserved")
        if name in self._tools:
            raise ValueError(f"duplicate skill tool name: {name!r}")
        self._tools[name] = tool

    def tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def resolve(self, name: str, runtime: Any = None) -> list[BaseTool]:  # noqa: ANN401, ARG002
        tool = self._tools.get(name)
        return [tool] if tool is not None else []


SKILL_TOOL_REGISTRY = SkillToolRegistry()
# Platform-owned skill tools are registered here, for example:
# SKILL_TOOL_REGISTRY.register(some_tool)
