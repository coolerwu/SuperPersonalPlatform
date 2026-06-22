import asyncio
import json

import pytest

from server.app.critique_service import CritiqueService
from server.domain.agents import AgentDefinition, HarnessMode, ModelDefinition
from server.domain.harness import Agent


class FakeAgentChatService:
    def __init__(self) -> None:
        self.bind_calls: list[dict[str, str | None]] = []

    def bind_prompt_agent(
        self,
        *,
        agent_id: str,
        name: str,
        system_prompt: str,
        model_id: str | None = None,
    ) -> Agent:
        self.bind_calls.append(
            {
                "agent_id": agent_id,
                "name": name,
                "system_prompt": system_prompt,
                "model_id": model_id,
            }
        )
        return Agent(
            definition=AgentDefinition(
                id=agent_id,
                name=name,
                system_prompt=system_prompt,
                model_id=model_id or "default",
            ),
            model=ModelDefinition(
                id=model_id or "default",
                name="Prompt Model",
                base_url="https://example.test/v1",
                api_key="secret",
                model="test-model",
                mode=HarnessMode.PROMPT,
            ),
        )


def create_disciplines(service: CritiqueService):
    economics = service.create_discipline(
        name="经济学",
        known_scope="微观决策与机会成本",
        critique_focus="成本、激励和替代方案",
        default_enabled=True,
    )
    psychology = service.create_discipline(
        name="心理学",
        known_scope="认知偏差与动机",
        critique_focus="自我欺骗和逃避行为",
        default_enabled=True,
    )
    return economics, psychology


def test_create_update_and_delete_discipline_persists_fields(tmp_path) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())

    created = service.create_discipline(
        name=" 经济学 ",
        known_scope=" 微观决策 ",
        critique_focus=" 机会成本 ",
        default_enabled=True,
    )

    assert created.name == "经济学"
    assert service.list_disciplines() == (created,)
    raw = json.loads(
        (tmp_path / "critique" / "disciplines.json").read_text(encoding="utf-8")
    )
    assert raw[0]["known_scope"] == "微观决策"
    assert not (tmp_path / "critique" / "disciplines.json.tmp").exists()

    updated = service.update_discipline(
        created.id,
        name="经济学",
        known_scope="微观与行为经济学",
        critique_focus="成本与激励",
        default_enabled=False,
    )
    assert updated.default_enabled is False
    assert updated.known_scope == "微观与行为经济学"

    service.delete_discipline(created.id)
    assert service.list_disciplines() == ()


def test_discipline_validation_rejects_empty_and_duplicate_names(tmp_path) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())
    service.create_discipline("经济学", "微观", "成本", True)

    with pytest.raises(ValueError, match="学科名称不能为空"):
        service.create_discipline(" ", "范围", "方向", True)
    with pytest.raises(ValueError, match="学科名称已存在"):
        service.create_discipline(" 经济学 ", "宏观", "激励", False)


def test_run_critique_fans_out_concurrently_and_preserves_selection_order(
    tmp_path, monkeypatch
) -> None:
    agent_service = FakeAgentChatService()
    service = CritiqueService(tmp_path, agent_service)
    economics, psychology = create_disciplines(service)
    active = 0
    max_active = 0

    async def fake_run_agent(request):
        nonlocal active, max_active
        if request.agent.definition.id == "critique-judge":
            return json.dumps(
                {
                    "weakest_assumption": "能力判断缺少行为证据",
                    "largest_disagreement": "收益机会与逃避动机的解释冲突",
                    "recommended_validation": "先做四周真实用户验证",
                },
                ensure_ascii=False,
            )
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        name = request.agent.definition.name
        return json.dumps(
            {
                "core_assumption": f"{name}核心假设",
                "counterevidence": f"{name}反证",
                "opportunity_cost": f"{name}机会成本",
                "key_question": f"{name}关键追问",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)

    result = asyncio.run(
        service.run_critique(
            "我是否应该辞职做自己的产品？",
            (psychology.id, economics.id),
        )
    )

    assert max_active == 2
    assert [item.discipline_id for item in result.results] == [
        psychology.id,
        economics.id,
    ]
    assert all(item.status == "completed" for item in result.results)
    assert result.status == "completed"
    assert result.judgment is not None
    assert result.judgment.weakest_assumption == "能力判断缺少行为证据"
    assert service.get_run(result.id) == result
    assert service.list_runs()[0].id == result.id
    assert len(agent_service.bind_calls) == 3
    assert "每个字段使用 30-60 个汉字" in agent_service.bind_calls[0]["system_prompt"]
    assert "每个字段不超过 100 个汉字" in agent_service.bind_calls[-1]["system_prompt"]


def test_run_critique_keeps_successes_when_one_discipline_returns_invalid_json(
    tmp_path, monkeypatch
) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())
    economics, psychology = create_disciplines(service)

    async def fake_run_agent(request):
        if request.agent.definition.id == f"critique-discipline-{psychology.id}":
            return "not json"
        if request.agent.definition.id == "critique-judge":
            return """```json
            {"weakest_assumption":"证据不足","largest_disagreement":"暂无",
             "recommended_validation":"补充用户访谈"}
            ```"""
        return json.dumps(
            {
                "core_assumption": "收益可持续",
                "counterevidence": "没有付费数据",
                "opportunity_cost": "放弃稳定现金流",
                "key_question": "谁会持续付费？",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)

    result = asyncio.run(
        service.run_critique("是否辞职？", (economics.id, psychology.id))
    )

    assert result.status == "partial"
    assert result.results[0].status == "completed"
    assert result.results[1].status == "failed"
    assert result.results[1].error == "模型返回的 JSON 无效"
    assert result.judgment is not None
    assert result.judgment.recommended_validation == "补充用户访谈"


def test_run_critique_rejects_empty_question_and_unknown_disciplines(tmp_path) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())

    with pytest.raises(ValueError, match="问题不能为空"):
        asyncio.run(service.run_critique(" ", ("missing",)))
    with pytest.raises(ValueError, match="至少选择一个学科"):
        asyncio.run(service.run_critique("问题", ()))
    with pytest.raises(ValueError, match="学科不存在"):
        asyncio.run(service.run_critique("问题", ("missing",)))


def test_retry_failed_discipline_uses_saved_snapshot_and_reruns_judge(
    tmp_path, monkeypatch
) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())
    economics = service.create_discipline("经济学", "微观决策", "机会成本", True)
    attempts = 0

    async def fake_run_agent(request):
        nonlocal attempts
        if request.agent.definition.id == "critique-judge":
            return json.dumps(
                {
                    "weakest_assumption": "需求未经验证",
                    "largest_disagreement": "暂无",
                    "recommended_validation": "先完成预售",
                },
                ensure_ascii=False,
            )
        attempts += 1
        if attempts == 1:
            return "invalid"
        return json.dumps(
            {
                "core_assumption": "用户愿意付费",
                "counterevidence": "目前只有口头反馈",
                "opportunity_cost": "放弃工资",
                "key_question": "谁已经付费？",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)
    failed = asyncio.run(service.run_critique("是否辞职？", (economics.id,)))
    assert failed.status == "failed"
    service.delete_discipline(economics.id)

    retried = asyncio.run(service.retry_discipline(failed.id, economics.id))

    assert retried.status == "completed"
    assert retried.results[0].analysis is not None
    assert retried.results[0].analysis.key_question == "谁已经付费？"
    assert retried.judgment is not None
    assert retried.disciplines == (economics,)


def test_follow_up_reuses_saved_disciplines_and_persists_context(
    tmp_path, monkeypatch
) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())
    economics, psychology = create_disciplines(service)
    prompts: list[tuple[str, str]] = []

    async def fake_run_agent(request):
        prompts.append((request.agent.definition.id, request.content))
        if request.agent.definition.id == "critique-judge":
            return json.dumps(
                {
                    "weakest_assumption": "付费需求仍未得到验证",
                    "largest_disagreement": "收益潜力与风险承受能力冲突",
                    "recommended_validation": "先完成小额预售",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "core_assumption": "预算足以完成核心验证",
                "counterevidence": "获客成本可能快速耗尽预算",
                "opportunity_cost": "两万元也可用于延长现金流",
                "key_question": "哪个实验最能改变当前判断？",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("server.app.critique_service.run_agent", fake_run_agent)
    initial = asyncio.run(
        service.run_critique(
            "我是否应该辞职做自己的产品？",
            (economics.id, psychology.id),
        )
    )
    prompts.clear()

    conversation = asyncio.run(
        service.follow_up(initial.id, "如果预算只有两万元，先验证什么？")
    )

    assert conversation.title == "我是否应该辞职做自己的产品？"
    assert [turn.question for turn in conversation.turns] == [
        "我是否应该辞职做自己的产品？",
        "如果预算只有两万元，先验证什么？",
    ]
    assert conversation.disciplines == (economics, psychology)
    assert service.get_run(conversation.id) == conversation
    discipline_prompts = [
        content for agent_id, content in prompts if agent_id.startswith("critique-discipline-")
    ]
    assert len(discipline_prompts) == 2
    assert all("我是否应该辞职做自己的产品？" in content for content in discipline_prompts)
    assert all("付费需求仍未得到验证" in content for content in discipline_prompts)
    assert all("如果预算只有两万元，先验证什么？" in content for content in discipline_prompts)


def test_get_run_synthesizes_turn_for_legacy_single_run_file(tmp_path) -> None:
    service = CritiqueService(tmp_path, FakeAgentChatService())
    economics = service.create_discipline("经济学", "微观决策", "机会成本", True)
    runs_dir = tmp_path / "critique" / "runs"
    runs_dir.mkdir(parents=True)
    runs_dir.joinpath("r-legacy.json").write_text(
        json.dumps(
            {
                "id": "r-legacy",
                "question": "旧问题",
                "model_id": "fast",
                "disciplines": [
                    {
                        "id": economics.id,
                        "name": economics.name,
                        "known_scope": economics.known_scope,
                        "critique_focus": economics.critique_focus,
                        "default_enabled": economics.default_enabled,
                        "created_at": economics.created_at,
                        "updated_at": economics.updated_at,
                    }
                ],
                "results": [],
                "judgment": None,
                "status": "completed",
                "created_at": "2026-06-21T00:00:00Z",
                "updated_at": "2026-06-21T00:01:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    conversation = service.get_run("r-legacy")

    assert conversation.title == "旧问题"
    assert len(conversation.turns) == 1
    assert conversation.turns[0].question == "旧问题"
