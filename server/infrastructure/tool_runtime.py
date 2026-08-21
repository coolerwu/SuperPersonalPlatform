from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.context_knowledge_service import ContextKnowledgeService
from server.domain.tooling import get_tool_definition


def build_platform_tools(tool_ids: tuple[str, ...], *, context_workspace: Path) -> list[Any]:
    tools = []
    service = ContextKnowledgeService(context_workspace)
    for tool_id in tool_ids:
        definition = get_tool_definition(tool_id)
        if definition.id == "search_context":
            tools.append(_search_context_tool(service))
        elif definition.id == "write_context":
            tools.append(_write_context_tool(service))
    return tools


def _search_context_tool(service: ContextKnowledgeService) -> Any:
    from langchain_core.tools import StructuredTool

    def search_context(query: str, top_k: int = 5) -> str:
        """Search the local knowledge context.

        Use this to retrieve relevant notes from workspace/context/knowledge/files before answering.
        """
        hits = service.search(query, top_k=top_k)
        if not hits:
            return "No matching context knowledge found."
        return json.dumps(
            {
                "hits": [
                    {"path": hit.path, "score": hit.score, "snippet": hit.snippet}
                    for hit in hits
                ]
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        search_context,
        name="search_context",
        description="Search workspace/context/knowledge/files for relevant local knowledge. Args: query, top_k.",
    )


def _write_context_tool(service: ContextKnowledgeService) -> Any:
    from langchain_core.tools import StructuredTool

    def write_context(type: str, absolute_path: str, content: str, mode: str = "append") -> str:
        """Write approved knowledge into the local context.

        Only call this after the user explicitly asks or confirms that this content should be saved.
        absolute_path is a tool path such as /files/wechat.md, not a filesystem path.
        mode must be append, overwrite, or create.
        """
        result = service.write(type=type, absolute_path=absolute_path, content=content, mode=mode)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        write_context,
        name="write_context",
        description=(
            "Write approved knowledge to workspace/context/knowledge/files. "
            "Args: type='knowledge', absolute_path like '/files/wechat.md', content, mode append|overwrite|create. "
            "Only use after explicit user approval."
        ),
    )
