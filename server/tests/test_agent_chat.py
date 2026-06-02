import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.adapter.agent_routes import create_agent_router
from server.adapter.auth_routes import create_auth_router
from server.adapter.dependencies import AppContainer
from server.app.agent_chat_service import (
    AgentChatService,
)
from server.app.agent_skill_service import AgentSkillService
from server.app.agent_tool_service import AgentToolRegistry, AgentToolRuntime
from server.app.auth_service import AuthService
from server.app.config_file_service import ConfigFileService
from server.domain.agents import AgentConfigError, ModelDefinition
from server.domain.auth import AuthToken
from server.domain.harness import (
    Agent,
    AgentToolCall,
    AgentToolCallingUnsupportedError,
    AgentToolReasoningResult,
    AgentToolResult,
    ChatOptions,
    ChatImage,
    PromptSkillContext,
    ReactSkillContext,
    run_agent,
)
from server.infrastructure.config import load_settings, parse_settings
from server.infrastructure.session import SessionCodec


class FakeModelGateway:
    def __init__(self, *, fail_tools: bool = False) -> None:
        self.calls = []
        self.reason_calls = []
        self.tool_results = []
        self.fail_tools = fail_tools

    async def complete(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        self.calls.append(
            {
                "model": model.id,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "images": images,
            }
        )
        return f"{model.model}: {system_prompt[:7]} / {user_message} / images={len(images)}"

    async def complete_with_tools(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        tool_names,
        skill_tools,
        images: tuple[ChatImage, ...] = (),
        max_iterations: int = 60,
    ) -> str:
        messages = ()
        for _ in range(max_iterations):
            result = await self.reason_with_tools(
                model,
                system_prompt,
                user_message,
                tool_names,
                messages,
                images,
            )
            messages = result.messages
            if not result.tool_calls:
                return result.content
            tool_results = []
            for tool_call in result.tool_calls:
                if tool_call.name == "list_skill":
                    content = await skill_tools.list_skill()
                elif tool_call.name == "read_skill":
                    content = await skill_tools.read_skill(str(tool_call.args.get("id") or ""))
                else:
                    content = f"Unsupported tool: {tool_call.name}"
                tool_results.append(AgentToolResult(tool_call.id, content))
            messages = self.append_tool_results(messages, tuple(tool_results))
        return await self.force_tool_final(model, messages)

    async def reason_with_tools(
        self,
        model: ModelDefinition,
        system_prompt: str,
        user_message: str,
        tool_names,
        messages,
        images: tuple[ChatImage, ...] = (),
    ) -> AgentToolReasoningResult:
        if self.fail_tools:
            raise AgentToolCallingUnsupportedError("当前模型不支持 LangChain tools")
        self.reason_calls.append(
            {
                "model": model.id,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "tool_names": tool_names,
                "images": images,
                "messages": messages,
            }
        )
        if self.tool_results:
            return AgentToolReasoningResult(
                content=f"tool answer: {self.tool_results[-1].content}",
                tool_calls=(),
                messages=tuple(messages) + ("final",),
            )
        return AgentToolReasoningResult(
            content="",
            tool_calls=(
                AgentToolCall(id="call-list", name="list_skill", args={}),
                AgentToolCall(id="call-read", name="read_skill", args={"id": "common:writing"}),
            ),
            messages=("reason",),
        )

    def append_tool_results(self, messages, tool_results) -> tuple:
        self.tool_results.extend(tool_results)
        return tuple(messages) + tuple(tool_results)

    async def force_tool_final(self, model: ModelDefinition, messages) -> str:
        return "forced final"


def write_config(
    workspace: Path,
    *,
    default_model_id: str = "fast",
    default_agent_id: str = "assistant",
    agent_model_id: str = "fast",
    supports_images: bool = True,
    common_tools: tuple[str, ...] = (),
    skill_ids: tuple[str, ...] = (),
) -> None:
    common_tools_yaml = "\n".join(f"    - {tool}" for tool in common_tools)
    skill_ids_yaml = "\n".join(f"      - {skill_id}" for skill_id in skill_ids)
    common_skills_section = (
        f"""
common_skills:
  tools:
{common_tools_yaml}
"""
        if common_tools_yaml
        else ""
    )
    skill_ids_section = (
        f"""
      skill_ids:
{skill_ids_yaml}
"""
        if skill_ids_yaml
        else ""
    )
    workspace.joinpath("config.yaml").write_text(
        f"""
auth:
  token: secret-token
proxy:
  upstream_base_url: http://example.test/
llm:
  default_model_id: {default_model_id}
  models:
    - id: fast
      name: Fast Model
      base_url: https://llm.example.test/v1
      api_key: top-secret-key
      model: fast-chat
      temperature: 0.2
      supports_images: {str(supports_images).lower()}
{common_skills_section.rstrip()}
agents:
  default_agent_id: {default_agent_id}
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: You are concise.
      model_id: {agent_model_id}
{skill_ids_section.rstrip()}
""".strip(),
        encoding="utf-8",
    )


def make_client(workspace: Path, gateway: FakeModelGateway | None = None) -> TestClient:
    config_service = ConfigFileService(workspace)
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=config_service,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        session_codec=SessionCodec("secret-token"),
        agent_chat_service=AgentChatService(config_service.config_path, gateway or FakeModelGateway()),
    )
    app = FastAPI()
    app.include_router(create_auth_router(container))
    app.include_router(create_agent_router(container))
    return TestClient(app)


def receive_until(websocket, message_type: str):
    while True:
        message = websocket.receive_json()
        if message["type"] == message_type:
            return message


def test_agent_config_defaults_without_permission_gate() -> None:
    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "proxy": {"upstream_base_url": "http://example.test/"},
        }
    )

    assert settings.agent_platform.models == ()
    assert settings.agent_platform.agents == ()
    assert settings.agent_platform.common_skill_tools == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"default_model_id": "missing"}, "llm.default_model_id"),
        ({"default_agent_id": "missing"}, "agents.default_agent_id"),
        ({"agent_model_id": "missing"}, "model_id"),
    ],
)
def test_agent_config_rejects_invalid_references(tmp_path, overrides, message) -> None:
    write_config(tmp_path, **overrides)

    with pytest.raises(ValueError, match=message):
        load_settings(tmp_path / "config.yaml")


def test_agent_options_require_auth_and_do_not_leak_api_key(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)

    assert client.get("/api/agents/options").status_code == 401
    assert client.post("/api/auth/login", json={"token": "secret-token"}).status_code == 200

    response = client.get("/api/agents/options")

    assert response.status_code == 200
    body = response.json()
    assert body["default_agent_id"] == "assistant"
    assert "top-secret-key" not in str(body)
    assert body["agents"][0] == {
        "id": "assistant",
        "name": "Assistant",
        "model_id": "fast",
        "model": {
            "id": "fast",
            "name": "Fast Model",
            "model": "fast-chat",
            "base_url": "https://llm.example.test/v1",
            "supports_images": True,
            "has_api_key": True,
        },
    }
    # Built-in agents are appended
    assert body["agents"][1]["id"] == "ai-investment-advisor"
    assert "top-secret-key" not in str(body)


def test_agent_config_endpoint_masks_api_keys(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.get("/api/agents/config")

    assert response.status_code == 200
    body = response.json()
    assert body["path"].endswith("config.yaml")
    assert "top-secret-key" not in str(body)
    assert body["models"][0]["has_api_key"] is True
    assert body["models"][0]["api_key_mask"] == "********"


def test_agent_config_update_preserves_existing_api_key(tmp_path) -> None:
    write_config(tmp_path, common_tools=("list_skill", "read_skill"), skill_ids=("common:writing",))
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put(
        "/api/agents/config",
        json={
            "default_model_id": "fast",
            "default_agent_id": "assistant",
            "common_skill_tools": ["list_skill", "read_skill"],
            "models": [
                {
                    "id": "fast",
                    "name": "Renamed Model",
                    "base_url": "https://llm.example.test/v1",
                    "model": "fast-chat",
                    "api_key": "",
                    "temperature": 0.4,
                    "supports_images": False,
                }
            ],
            "agents": [
                {
                    "id": "assistant",
                    "name": "Assistant",
                    "model_id": "fast",
                    "system_prompt": "You are direct.",
                    "skill_ids": ["common:writing"],
                }
            ],
        },
    )

    assert response.status_code == 200
    settings = load_settings(tmp_path / "config.yaml")
    assert settings.agent_platform.get_model("fast").api_key == "top-secret-key"
    assert settings.agent_platform.get_model("fast").name == "Renamed Model"
    assert settings.agent_platform.get_model("fast").supports_images is False
    assert settings.agent_platform.common_skill_tools == ("list_skill", "read_skill")
    assert settings.agent_platform.get_agent("assistant").skill_ids == ("common:writing",)


def test_agent_chat_websocket_uses_agent_bound_model_without_model_id(tmp_path) -> None:
    write_config(tmp_path)
    gateway = FakeModelGateway()
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        assert websocket.receive_json() == {"type": "status", "status": "connected"}
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "你好",
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        checkpoint = websocket.receive_json()
        assert checkpoint["type"] == "checkpoint"
        assert checkpoint["stage"] == "answer"
        assert receive_until(websocket, "assistant_message") == {
            "type": "assistant_message",
            "content": "fast-chat: You are / 你好 / images=0",
        }
        assert websocket.receive_json() == {"type": "status", "status": "idle"}

    assert [call["model"] for call in gateway.calls] == ["fast"]
    assert gateway.calls[0]["system_prompt"] == "You are concise."
    assert gateway.calls[0]["user_message"] == "你好"


def test_agent_chat_websocket_passes_images_to_adapter(tmp_path) -> None:
    write_config(tmp_path, supports_images=True)
    gateway = FakeModelGateway()
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "看图",
                "images": [{"mime_type": "image/png", "data": "aW1hZ2U="}],
            }
        )
        websocket.receive_json()
        assert receive_until(websocket, "assistant_message")["content"].endswith("images=1")

    assert gateway.calls[0]["images"] == (ChatImage(mime_type="image/png", data="aW1hZ2U="),)


def test_agent_chat_websocket_rejects_images_for_text_model(tmp_path) -> None:
    write_config(tmp_path, supports_images=False)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "看图",
                "images": [{"mime_type": "image/png", "data": "aW1hZ2U="}],
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        assert websocket.receive_json() == {
            "type": "error",
            "message": "当前模型不支持图片输入",
        }


def test_agent_chat_requires_agent_id(tmp_path) -> None:
    write_config(tmp_path)
    service = AgentChatService(tmp_path / "config.yaml", FakeModelGateway())

    with pytest.raises(AgentConfigError, match="agent_id is required"):
        asyncio.run(service.run_agent("", PromptSkillContext(content="你好")))


def test_agent_chat_falls_back_to_sequential_goal_confirmation_without_langgraph(
    tmp_path,
    monkeypatch,
) -> None:
    write_config(tmp_path)
    gateway = FakeModelGateway()
    service = AgentChatService(tmp_path / "config.yaml", gateway)
    platform = load_settings(tmp_path / "config.yaml").agent_platform

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "langgraph.graph":
            raise ImportError("langgraph unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    agent = platform.get_agent("assistant")
    result = asyncio.run(
        run_agent(
            Agent(
                definition=agent,
                model=platform.get_model("fast"),
                llm_client=gateway,
            ),
            skill_context=ReactSkillContext(
                content="整理今天任务",
                tool_registry=service._tool_registry,
                tool_runtime=AgentToolRuntime(skill_tools=AgentSkillService(tmp_path).toolbox(agent)),
            ),
            options=ChatOptions(),
        )
    )

    assert result == "fast-chat: You are / 整理今天任务 / images=0"
    assert gateway.calls[0]["system_prompt"] == "You are concise."
    assert gateway.calls[0]["user_message"] == "整理今天任务"


def test_agent_config_rejects_unknown_common_skill_tool(tmp_path) -> None:
    write_config(tmp_path, common_tools=("list_skill", "shell"))

    with pytest.raises(ValueError, match="common_skills.tools"):
        load_settings(tmp_path / "config.yaml")


def test_agent_tool_config_resolves_profile_allow_and_deny(tmp_path) -> None:
    write_config(tmp_path)
    raw = load_settings(tmp_path / "config.yaml")
    agent = raw.agent_platform.get_agent("assistant")
    registry = AgentToolRegistry()

    settings = parse_settings(
        {
            "auth": {"token": "secret-token"},
            "proxy": {"upstream_base_url": "http://example.test/"},
            "tools": {
                "profile": "self-dev",
                "allow": ["repo_push"],
                "deny": ["repo_push", "repo_write_file"],
            },
            "llm": {
                "models": [
                    {
                        "id": "fast",
                        "name": "Fast",
                        "base_url": "https://llm.example.test/v1",
                        "api_key": "key",
                        "model": "fast-chat",
                    }
                ]
            },
            "agents": {
                "definitions": [
                    {
                        "id": "assistant",
                        "name": "Assistant",
                        "system_prompt": "You are concise.",
                        "model_id": "fast",
                    }
                ]
            },
        }
    )

    tool_names = registry.resolve_tools(
        settings.agent_platform.tools,
        settings.agent_platform.get_agent("assistant"),
        (),
    )

    assert "repo_read_file" in tool_names
    assert "repo_push" not in tool_names
    assert "repo_write_file" not in tool_names
    assert registry.resolve_tools(raw.agent_platform.tools, agent, ("list_skill",)) == ("list_skill",)


def test_agent_tool_config_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="unsupported tool"):
        parse_settings(
            {
                "auth": {"token": "secret-token"},
                "proxy": {"upstream_base_url": "http://example.test/"},
                "tools": {"allow": ["not_a_tool"]},
            }
        )


def test_agent_skill_service_lists_and_reads_bound_common_and_private_skills(tmp_path) -> None:
    write_config(
        tmp_path,
        common_tools=("list_skill", "read_skill"),
        skill_ids=("common:writing", "private:daily"),
    )
    common_dir = tmp_path / "skills" / "common"
    private_dir = tmp_path / "skills" / "agents" / "assistant"
    common_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    (common_dir / "writing.md").write_text(
        "# 写作技能\n用于整理文章。\n第二行摘要。\n",
        encoding="utf-8",
    )
    (private_dir / "daily.md").write_text("# 日常技能\n处理每日任务。", encoding="utf-8")
    platform = load_settings(tmp_path / "config.yaml").agent_platform
    service = AgentSkillService(tmp_path)

    skills = service.list_skills(platform.get_agent("assistant"))
    assert [skill.id for skill in skills] == ["common:writing", "private:daily"]
    assert skills[0].name == "写作技能"
    assert "用于整理文章" in skills[0].summary

    content = service.read_skill(platform.get_agent("assistant"), "private:daily")
    assert content.name == "日常技能"
    assert "处理每日任务" in content.content


def test_agent_skill_service_reads_directory_skill_md(tmp_path) -> None:
    write_config(tmp_path, skill_ids=("common:writing", "private:self-dev"))
    (tmp_path / "skills" / "common" / "writing").mkdir(parents=True)
    (tmp_path / "skills" / "common" / "writing" / "SKILL.md").write_text(
        "# 写作目录技能\n目录式 skill。",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "agents" / "assistant" / "self-dev").mkdir(parents=True)
    (tmp_path / "skills" / "agents" / "assistant" / "self-dev" / "SKILL.md").write_text(
        "# 自开发\n按流程开发。",
        encoding="utf-8",
    )
    platform = load_settings(tmp_path / "config.yaml").agent_platform
    service = AgentSkillService(tmp_path)

    common = service.read_skill(platform.get_agent("assistant"), "common:writing")
    private = service.read_skill(platform.get_agent("assistant"), "private:self-dev")

    assert common.name == "写作目录技能"
    assert "目录式 skill" in common.content
    assert private.name == "自开发"


def test_repo_tools_reject_paths_outside_task_repo(tmp_path) -> None:
    write_config(tmp_path, skill_ids=("common:writing",))
    repo = tmp_path / "repo"
    repo.mkdir()
    platform = load_settings(tmp_path / "config.yaml").agent_platform
    runtime = AgentToolRuntime(
        skill_tools=AgentSkillService(tmp_path).toolbox(platform.get_agent("assistant")),
        repo_root=repo,
    )

    with pytest.raises(ValueError, match="escapes task repo"):
        runtime.resolve_repo_path("../config.yaml")


def test_agent_skill_service_rejects_unbound_or_unsafe_skill(tmp_path) -> None:
    write_config(tmp_path, skill_ids=("common:writing",))
    platform = load_settings(tmp_path / "config.yaml").agent_platform
    service = AgentSkillService(tmp_path)

    with pytest.raises(ValueError, match="not enabled"):
        service.read_skill(platform.get_agent("assistant"), "common:other")
    with pytest.raises(ValueError, match="Invalid skill id"):
        service.read_skill(platform.get_agent("assistant"), "../secret")


def test_agent_chat_uses_langchain_skill_tools_when_configured(tmp_path) -> None:
    write_config(
        tmp_path,
        common_tools=("list_skill", "read_skill"),
        skill_ids=("common:writing",),
    )
    skills_dir = tmp_path / "skills" / "common"
    skills_dir.mkdir(parents=True)
    (skills_dir / "writing.md").write_text("# 写作\n用中文润色。", encoding="utf-8")
    gateway = FakeModelGateway()
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "帮我润色",
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        assert receive_until(websocket, "assistant_message")["content"].startswith("tool answer:")
        assert websocket.receive_json() == {"type": "status", "status": "idle"}

    assert len(gateway.calls) == 0  # tools configured, reason uses reason_with_tools not complete
    assert gateway.reason_calls[0]["tool_names"] == ("list_skill", "read_skill")
    assert gateway.reason_calls[1]["messages"]
    assert any("common:writing" in result.content for result in gateway.tool_results)
    assert any("用中文润色" in result.content for result in gateway.tool_results)


def test_agent_chat_returns_error_when_tool_calling_is_unsupported(tmp_path) -> None:
    write_config(tmp_path, common_tools=("list_skill",), skill_ids=("common:writing",))
    gateway = FakeModelGateway(fail_tools=True)
    client = make_client(tmp_path, gateway)
    client.post("/api/auth/login", json={"token": "secret-token"})

    with client.websocket_connect("/api/agents/chat/connect") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "message",
                "agent_id": "assistant",
                "content": "帮我润色",
            }
        )
        assert websocket.receive_json() == {"type": "status", "status": "running"}
        assert receive_until(websocket, "error") == {
            "type": "error",
            "message": "当前模型不支持 LangChain tools",
        }
