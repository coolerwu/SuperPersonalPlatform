import asyncio
import json
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
    AgentToolCall,
    AgentToolCallingUnsupportedError,
    AgentToolReasoningResult,
    AgentToolResult,
    ChatImage,
    HarnessMode,
)
from server.infrastructure.config import load_settings, parse_settings
from server.domain.harness import runner as harness_runner
from server.infrastructure.session import SessionCodec


class FakeModelGateway:
    def __init__(self, *, fail_tools: bool = False) -> None:
        self.calls = []
        self.reason_calls = []
        self.tool_results = []
        self.fail_tools = fail_tools
        self.model = None

    def bind(self, model: ModelDefinition):
        self.model = model
        return self

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        images: tuple[ChatImage, ...] = (),
    ) -> str:
        self.calls.append(
            {
                "model": self.model.id,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "images": images,
            }
        )
        if "目标契约" in system_prompt:
            return (
                '{"goal":"完成用户请求","completion_criteria":["给出有效回答"],'
                '"output_format":"plain text","required_evidence":[]}'
            )
        if "独立验证器" in system_prompt:
            return '{"passed":true,"blocked":false,"feedback":""}'
        if "候选输出已经通过验证" in system_prompt:
            return str(json.loads(user_message)["candidate"])
        return f"{self.model.model}: {system_prompt[:7]} / {user_message} / images={len(images)}"

    async def complete_with_tools(
        self,
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
        return await self.force_tool_final(messages)

    async def reason_with_tools(
        self,
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
                "model": self.model.id,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "tool_names": tool_names,
                "images": images,
                "messages": messages,
            }
        )
        if not tool_names:
            return AgentToolReasoningResult(
                content="candidate without tools",
                tool_calls=(),
                messages=tuple(messages) + ("candidate",),
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

    async def force_tool_final(self, messages) -> str:
        return "forced final"


def write_config(
    workspace: Path,
    *,
    default_model_id: str = "fast",
    default_agent_id: str = "assistant",
    agent_model_id: str = "fast",
    supports_images: bool = True,
    mode: str | None = None,
    common_tools: tuple[str, ...] = (),
    skill_ids: tuple[str, ...] = (),
) -> None:
    mode_line = f"\n      mode: {mode}" if mode is not None else ""
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
{mode_line}
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
    gateway = gateway or FakeModelGateway()
    harness_runner.create_model_runner = lambda model: gateway.bind(model)
    config_service = ConfigFileService(workspace)
    container = AppContainer(
        auth_service=AuthService(AuthToken("secret-token")),
        config_file_service=config_service,
        proxy_service=None,
        system_log_service=None,
        system_update_service=None,
        session_codec=SessionCodec("secret-token"),
        agent_chat_service=AgentChatService(config_service.config_path),
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
    assert "default_agent_id" not in body
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
            "mode": "prompt",
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
    assert "default_agent_id" not in body
    assert "top-secret-key" not in str(body)
    assert body["models"][0]["has_api_key"] is True
    assert body["models"][0]["api_key_mask"] == "********"
    assert body["models"][0]["mode"] == "prompt"


def test_agent_config_parses_agent_mode_and_rejects_unknown_mode(tmp_path) -> None:
    write_config(tmp_path, mode="agent")

    assert load_settings(tmp_path / "config.yaml").agent_platform.get_model("fast").mode is HarnessMode.AGENT

    write_config(tmp_path, mode="automatic")
    with pytest.raises(AgentConfigError, match=r"llm.models\[fast\].mode"):
        load_settings(tmp_path / "config.yaml")


def test_agent_config_update_preserves_existing_api_key(tmp_path) -> None:
    write_config(tmp_path, common_tools=("list_skill", "read_skill"), skill_ids=("common:writing",))
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put(
        "/api/agents/config",
        json={
            "default_model_id": "fast",
            "common_skill_tools": ["list_skill", "read_skill"],
            "skills": [
                {
                    "id": "common:writing",
                },
                {
                    "id": "common:portfolio",
                }
            ],
            "models": [
                {
                    "id": "fast",
                    "name": "Renamed Model",
                    "base_url": "https://llm.example.test/v1",
                    "model": "fast-chat",
                    "mode": "agent",
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
    assert settings.agent_platform.get_model("fast").mode is HarnessMode.AGENT
    assert settings.agent_platform.common_skill_tools == ("list_skill", "read_skill")
    assert settings.agent_platform.skill_definitions[0].id == "common:writing"
    assert settings.agent_platform.skill_definitions[1].id == "common:portfolio"
    assert settings.agent_platform.get_agent("assistant").skill_ids == ("common:writing",)
    raw = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "agents:\n" in raw
    assert "default_agent_id" not in raw
    assert "skills:\n" in raw
    assert "skills:\n  definitions:\n  - id: common:writing\n  - id: common:portfolio\n" in raw


def test_agent_config_update_removes_legacy_default_agent(tmp_path) -> None:
    write_config(tmp_path)
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    response = client.put(
        "/api/agents/config",
        json={
            "default_model_id": "fast",
            "common_skill_tools": [],
            "skills": [],
            "models": [
                {
                    "id": "fast",
                    "name": "Fast Model",
                    "base_url": "https://llm.example.test/v1",
                    "model": "fast-chat",
                    "api_key": "",
                    "temperature": 0.2,
                    "supports_images": True,
                }
            ],
            "agents": [
                {
                    "id": "renamed-agent",
                    "name": "Renamed Agent",
                    "model_id": "fast",
                    "system_prompt": "You are direct.",
                    "skill_ids": [],
                }
            ],
        },
    )

    assert response.status_code == 200
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "default_agent_id" not in saved
    settings = load_settings(tmp_path / "config.yaml")
    assert settings.agent_platform.get_agent("renamed-agent").name == "Renamed Agent"


def test_agent_skill_content_endpoint_reads_and_writes_markdown(tmp_path) -> None:
    write_config(tmp_path, skill_ids=("common:research",))
    skill_dir = tmp_path / "skills" / "common" / "research"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# 研究\n旧内容", encoding="utf-8")
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"token": "secret-token"})

    read_response = client.get("/api/agents/skills/content", params={"id": "common:research"})

    assert read_response.status_code == 200
    assert read_response.json()["content"] == "# 研究\n旧内容"

    write_response = client.put(
        "/api/agents/skills/content",
        json={
            "id": "common:research",
            "name": "common:research",
            "content": "# 研究\n新内容",
            "tools": {"profile": "default", "allow": ["list_skill"], "deny": []},
        },
    )

    assert write_response.status_code == 200
    saved = skill_path.read_text(encoding="utf-8")
    assert saved.startswith("---\n")
    assert "profile: default" in saved
    assert "- list_skill" in saved
    assert saved.endswith("# 研究\n新内容")


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


def test_prompt_model_does_not_switch_mode_when_tools_are_available(tmp_path) -> None:
    write_config(
        tmp_path,
        mode="prompt",
        common_tools=("list_skill",),
        skill_ids=("common:writing",),
    )
    gateway = FakeModelGateway()
    harness_runner.create_model_runner = lambda model: gateway.bind(model)
    service = AgentChatService(tmp_path / "config.yaml")

    result = asyncio.run(service.chat("assistant", "直接回答"))

    assert result.endswith("/ 直接回答 / images=0")
    assert gateway.reason_calls == []


def test_bind_prompt_agent_uses_configured_default_model(tmp_path) -> None:
    write_config(tmp_path, mode="prompt")
    service = AgentChatService(tmp_path / "config.yaml")

    agent = service.bind_prompt_agent(
        agent_id="critique-economics",
        name="经济学",
        system_prompt="从经济学角度批判。",
    )

    assert agent.definition.id == "critique-economics"
    assert agent.definition.system_prompt == "从经济学角度批判。"
    assert agent.definition.model_id == "fast"
    assert agent.model.id == "fast"
    assert agent.model.mode is HarnessMode.PROMPT


def test_bind_prompt_agent_rejects_agent_mode_model(tmp_path) -> None:
    write_config(tmp_path, mode="agent")
    service = AgentChatService(tmp_path / "config.yaml")

    with pytest.raises(AgentConfigError, match="Prompt 模式"):
        service.bind_prompt_agent(
            agent_id="critique-economics",
            name="经济学",
            system_prompt="从经济学角度批判。",
        )


def test_agent_model_runs_strict_loop_without_tools(tmp_path) -> None:
    write_config(tmp_path, mode="agent")
    gateway = FakeModelGateway()
    harness_runner.create_model_runner = lambda model: gateway.bind(model)
    service = AgentChatService(tmp_path / "config.yaml")

    result = asyncio.run(service.chat("assistant", "严格回答"))

    assert result == "candidate without tools"
    assert gateway.reason_calls[0]["tool_names"] == ()


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
    gateway = FakeModelGateway()
    harness_runner.create_model_runner = lambda model: gateway.bind(model)
    service = AgentChatService(tmp_path / "config.yaml")

    with pytest.raises(AgentConfigError, match="agent_id is required"):
        asyncio.run(
            service.chat(
                "",
                "你好",
            )
        )


def test_agent_chat_preserves_config_error_for_empty_content(tmp_path) -> None:
    write_config(tmp_path, mode="prompt")
    service = AgentChatService(tmp_path / "config.yaml")

    with pytest.raises(AgentConfigError, match="消息内容不能为空"):
        asyncio.run(service.chat("assistant", "   "))


def test_agent_config_rejects_unknown_common_skill_tool(tmp_path) -> None:
    write_config(tmp_path, common_tools=("list_skill", "shell"))

    with pytest.raises(ValueError, match="common_skills.tools"):
        load_settings(tmp_path / "config.yaml")


def test_agent_tool_config_rejects_removed_self_dev_profile() -> None:
    with pytest.raises(ValueError, match="tools.profile is unsupported"):
        parse_settings(
        {
            "auth": {"token": "secret-token"},
            "proxy": {"upstream_base_url": "http://example.test/"},
            "tools": {
                "profile": "self-dev",
                "allow": [],
                "deny": [],
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


def test_agent_tools_are_resolved_from_bound_skill_frontmatter(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        """
auth:
  token: secret-token
proxy:
  upstream_base_url: http://example.test/
tools:
  profile: default
  allow: []
  deny: []
skills:
  definitions:
    - id: common:portfolio
      name: 资产组合
llm:
  models:
    - id: fast
      name: Fast
      base_url: https://llm.example.test/v1
      api_key: key
      model: fast-chat
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: You are concise.
      model_id: fast
      skill_ids:
        - common:portfolio
      tools:
        profile: portfolio
        allow: []
        deny: []
""".strip(),
        encoding="utf-8",
    )
    skill_dir = tmp_path / "skills" / "common" / "portfolio"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: 资产组合
tools:
  profile: portfolio
  allow: []
  deny:
    - delete_portfolio_holding
---
# 资产组合
""",
        encoding="utf-8",
    )
    service = AgentChatService(tmp_path / "config.yaml")
    settings = service._load_platform()
    registry = AgentToolRegistry()

    tool_names = registry.resolve_tools(
        settings.tools,
        settings.get_agent("assistant"),
        settings.common_skill_tools,
        settings.skill_definitions,
    )

    assert "list_portfolio_holdings" in tool_names
    assert "add_portfolio_holding" in tool_names
    assert "delete_portfolio_holding" not in tool_names


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
    write_config(tmp_path, skill_ids=("common:writing", "private:daily"))
    (tmp_path / "skills" / "common" / "writing").mkdir(parents=True)
    (tmp_path / "skills" / "common" / "writing" / "SKILL.md").write_text(
        "# 写作目录技能\n目录式 skill。",
        encoding="utf-8",
    )
    (tmp_path / "skills" / "agents" / "assistant" / "daily").mkdir(parents=True)
    (tmp_path / "skills" / "agents" / "assistant" / "daily" / "SKILL.md").write_text(
        "# 日常技能\n处理每日任务。",
        encoding="utf-8",
    )
    platform = load_settings(tmp_path / "config.yaml").agent_platform
    service = AgentSkillService(tmp_path)

    common = service.read_skill(platform.get_agent("assistant"), "common:writing")
    private = service.read_skill(platform.get_agent("assistant"), "private:daily")

    assert common.name == "写作目录技能"
    assert "目录式 skill" in common.content
    assert private.name == "日常技能"


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
        mode="agent",
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

    assert len(gateway.calls) == 3  # GOAL, independent VERIFY, and FINALIZE
    assert gateway.reason_calls[0]["tool_names"] == ("list_skill", "read_skill")
    assert gateway.reason_calls[1]["messages"]
    assert any("common:writing" in result.content for result in gateway.tool_results)
    assert any("用中文润色" in result.content for result in gateway.tool_results)


def test_agent_chat_returns_error_when_tool_calling_is_unsupported(tmp_path) -> None:
    write_config(
        tmp_path,
        mode="agent",
        common_tools=("list_skill",),
        skill_ids=("common:writing",),
    )
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
