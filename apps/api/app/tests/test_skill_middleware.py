from deepagents.backends.protocol import FileDownloadResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.skill_middleware import AgentSkillsMiddleware


class FakeBackend:
    def download_files(self, paths):
        return [FileDownloadResponse(path=path, content=b"Browser skill body") for path in paths]

    async def adownload_files(self, paths):
        return self.download_files(paths)


SKILL = {
    "path": "/skills/web-browser/SKILL.md",
    "name": "web-browser",
    "description": "browser",
    "metadata": {"include_tools": "browser_open browser_screenshot"},
}


def _middleware() -> AgentSkillsMiddleware:
    return AgentSkillsMiddleware(
        backend=FakeBackend(),
        sources=[("/skills/", "Platform")],
        enabled_skills={"web-browser"},
    )


def _summary() -> HumanMessage:
    return HumanMessage(
        content="summary", additional_kwargs={"lc_source": "summarization"}
    )


def test_repins_skill_after_compaction() -> None:
    middleware = _middleware()
    state = {"messages": [HumanMessage(content="hi"), _summary()], "skills_metadata": [SKILL]}

    update = middleware.before_model(state, None)

    assert update is not None
    messages = update["messages"]
    assert len(messages) == 1
    assert messages[0].additional_kwargs["lc_source"] == "pinned_skill"
    assert "Browser skill body" in messages[0].content


def test_does_not_repin_when_skill_still_in_context() -> None:
    middleware = _middleware()
    pinned = HumanMessage(
        content="<skill>body</skill>",
        additional_kwargs={"lc_source": "pinned_skill", "skill": {"path": SKILL["path"]}},
    )
    state = {"messages": [_summary(), pinned], "skills_metadata": [SKILL]}

    assert middleware.before_model(state, None) is None


def test_does_not_repin_when_skill_read_after_summary() -> None:
    middleware = _middleware()
    state = {
        "messages": [
            _summary(),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"file_path": SKILL["path"]},
                        "id": "call-1",
                    }
                ],
            ),
            ToolMessage(content="skill body", tool_call_id="call-1", name="read_file"),
        ],
        "skills_metadata": [SKILL],
    }

    assert middleware.before_model(state, None) is None


def test_does_not_repin_without_compaction() -> None:
    middleware = _middleware()
    state = {"messages": [HumanMessage(content="hi")], "skills_metadata": [SKILL]}

    assert middleware.before_model(state, None) is None


def test_repins_only_enabled_skills_with_tools() -> None:
    middleware = _middleware()
    disabled = {
        "path": "/skills/other/SKILL.md",
        "name": "other",
        "description": "other",
        "metadata": {"include_tools": "web_search"},
    }
    no_tools = {
        "path": "/skills/plain/SKILL.md",
        "name": "plain",
        "description": "plain",
        "metadata": {},
    }
    state = {
        "messages": [_summary()],
        "skills_metadata": [SKILL, disabled, no_tools],
    }

    update = middleware.before_model(state, None)

    assert update is not None
    assert len(update["messages"]) == 1
    assert update["messages"][0].additional_kwargs["skill"]["name"] == "web-browser"
