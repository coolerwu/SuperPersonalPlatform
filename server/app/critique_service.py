import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from server.app.agent_chat_service import AgentChatService
from server.domain.critique import (
    CritiqueAnalysis,
    CritiqueDiscipline,
    CritiqueDisciplineNotFoundError,
    CritiqueDisciplineResult,
    CritiqueJudgment,
    CritiqueRun,
    CritiqueRunNotFoundError,
    CritiqueTurn,
)
from server.domain.harness import HarnessRequest, run_agent


CritiqueEventCallback = Callable[[dict[str, object]], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CritiqueService:
    def __init__(self, workspace: Path, agent_chat_service: AgentChatService) -> None:
        self._dir = workspace / "critique"
        self._agent_chat_service = agent_chat_service

    def list_disciplines(self) -> tuple[CritiqueDiscipline, ...]:
        return tuple(self._discipline_from_dict(item) for item in self._read_json(self._disciplines_path(), []))

    def create_discipline(
        self,
        name: str,
        known_scope: str,
        critique_focus: str,
        default_enabled: bool = True,
    ) -> CritiqueDiscipline:
        normalized_name = name.strip()
        self._ensure_unique_name(normalized_name)
        now = _now_iso()
        discipline = CritiqueDiscipline(
            id=f"d-{uuid.uuid4().hex[:12]}",
            name=normalized_name,
            known_scope=known_scope.strip(),
            critique_focus=critique_focus.strip(),
            default_enabled=bool(default_enabled),
            created_at=now,
            updated_at=now,
        )
        disciplines = [*self.list_disciplines(), discipline]
        self._write_json(self._disciplines_path(), [asdict(item) for item in disciplines])
        return discipline

    def update_discipline(
        self,
        discipline_id: str,
        *,
        name: str,
        known_scope: str,
        critique_focus: str,
        default_enabled: bool,
    ) -> CritiqueDiscipline:
        disciplines = list(self.list_disciplines())
        normalized_name = name.strip()
        self._ensure_unique_name(normalized_name, exclude_id=discipline_id)
        for index, current in enumerate(disciplines):
            if current.id != discipline_id:
                continue
            updated = CritiqueDiscipline(
                id=current.id,
                name=normalized_name,
                known_scope=known_scope.strip(),
                critique_focus=critique_focus.strip(),
                default_enabled=bool(default_enabled),
                created_at=current.created_at,
                updated_at=_now_iso(),
            )
            disciplines[index] = updated
            self._write_json(self._disciplines_path(), [asdict(item) for item in disciplines])
            return updated
        raise CritiqueDisciplineNotFoundError("学科不存在")

    def delete_discipline(self, discipline_id: str) -> None:
        disciplines = list(self.list_disciplines())
        remaining = [item for item in disciplines if item.id != discipline_id]
        if len(remaining) == len(disciplines):
            raise CritiqueDisciplineNotFoundError("学科不存在")
        self._write_json(self._disciplines_path(), [asdict(item) for item in remaining])

    def list_runs(self) -> tuple[CritiqueRun, ...]:
        runs = [self._run_from_dict(self._read_json(path, {})) for path in self._runs_dir().glob("*.json")]
        return tuple(sorted(runs, key=lambda item: item.updated_at, reverse=True))

    def get_run(self, run_id: str) -> CritiqueRun:
        path = self._run_path(run_id)
        if not path.exists():
            raise CritiqueRunNotFoundError("批判记录不存在")
        return self._run_from_dict(self._read_json(path, {}))

    async def run_critique(
        self,
        question: str,
        discipline_ids: tuple[str, ...],
        *,
        model_id: str | None = None,
        on_event: CritiqueEventCallback | None = None,
    ) -> CritiqueRun:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        if not discipline_ids:
            raise ValueError("至少选择一个学科")
        discipline_map = {item.id: item for item in self.list_disciplines()}
        disciplines: list[CritiqueDiscipline] = []
        for discipline_id in discipline_ids:
            if discipline_id not in discipline_map:
                raise CritiqueDisciplineNotFoundError("学科不存在")
            disciplines.append(discipline_map[discipline_id])

        run_id = f"r-{uuid.uuid4().hex}"
        created_at = _now_iso()
        turn, resolved_model_id = await self._execute_turn(
            run_id=run_id,
            question=normalized_question,
            disciplines=tuple(disciplines),
            model_id=model_id,
            prior_turns=(),
            on_event=on_event,
        )
        run = CritiqueRun(
            id=run_id,
            title=normalized_question,
            question=turn.question,
            model_id=resolved_model_id,
            disciplines=tuple(disciplines),
            results=turn.results,
            judgment=turn.judgment,
            turns=(turn,),
            status=turn.status,
            created_at=created_at,
            updated_at=turn.updated_at,
        )
        self._write_json(self._run_path(run.id), self._run_to_dict(run))
        await self._emit(on_event, {"type": "run_completed", "run": self._run_to_dict(run)})
        return run

    async def follow_up(
        self,
        run_id: str,
        question: str,
        *,
        on_event: CritiqueEventCallback | None = None,
    ) -> CritiqueRun:
        current = self.get_run(run_id)
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        turn, resolved_model_id = await self._execute_turn(
            run_id=current.id,
            question=normalized_question,
            disciplines=current.disciplines,
            model_id=current.model_id or None,
            prior_turns=current.turns,
            on_event=on_event,
        )
        updated = CritiqueRun(
            id=current.id,
            title=current.title,
            question=turn.question,
            model_id=resolved_model_id,
            disciplines=current.disciplines,
            results=turn.results,
            judgment=turn.judgment,
            turns=(*current.turns, turn),
            status=turn.status,
            created_at=current.created_at,
            updated_at=turn.updated_at,
        )
        self._write_json(self._run_path(updated.id), self._run_to_dict(updated))
        await self._emit(on_event, {"type": "run_completed", "run": self._run_to_dict(updated)})
        return updated

    async def _execute_turn(
        self,
        *,
        run_id: str,
        question: str,
        disciplines: tuple[CritiqueDiscipline, ...],
        model_id: str | None,
        prior_turns: tuple[CritiqueTurn, ...],
        on_event: CritiqueEventCallback | None,
    ) -> tuple[CritiqueTurn, str]:
        turn_id = f"t-{uuid.uuid4().hex}"
        created_at = _now_iso()
        await self._emit(
            on_event,
            {"type": "run_started", "run_id": run_id, "turn_id": turn_id},
        )

        async def execute_discipline(discipline: CritiqueDiscipline) -> tuple[CritiqueDisciplineResult, str]:
            await self._emit(
                on_event,
                {
                    "type": "discipline_status",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "discipline_id": discipline.id,
                    "status": "running",
                },
            )
            try:
                agent = self._agent_chat_service.bind_prompt_agent(
                    agent_id=f"critique-discipline-{discipline.id}",
                    name=discipline.name,
                    system_prompt=self._discipline_prompt(discipline),
                    model_id=model_id,
                )
                response = await run_agent(
                    HarnessRequest.for_prompt(
                        agent=agent,
                        content=self._discipline_request(question, prior_turns),
                    )
                )
                analysis = self._parse_analysis(response)
                result = CritiqueDisciplineResult(
                    discipline_id=discipline.id,
                    discipline_name=discipline.name,
                    status="completed",
                    analysis=analysis,
                )
                resolved_model_id = agent.model.id
            except Exception as exc:
                result = CritiqueDisciplineResult(
                    discipline_id=discipline.id,
                    discipline_name=discipline.name,
                    status="failed",
                    error=self._safe_error(exc),
                )
                resolved_model_id = model_id or ""
            await self._emit(
                on_event,
                {
                    "type": "discipline_status",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "discipline_id": discipline.id,
                    "status": result.status,
                    "result": self._result_to_dict(result),
                },
            )
            return result, resolved_model_id

        executed = await asyncio.gather(
            *(execute_discipline(discipline) for discipline in disciplines),
            return_exceptions=False,
        )
        results = tuple(item[0] for item in executed)
        resolved_model_id = next((item[1] for item in executed if item[1]), model_id or "")
        successful = tuple(item for item in results if item.status == "completed")
        judgment: CritiqueJudgment | None = None
        judge_failed = False
        if successful:
            await self._emit(
                on_event,
                {"type": "judgment_status", "run_id": run_id, "turn_id": turn_id, "status": "running"},
            )
            try:
                judge = self._agent_chat_service.bind_prompt_agent(
                    agent_id="critique-judge",
                    name="综合裁判",
                    system_prompt=self._judge_prompt(),
                    model_id=model_id,
                )
                resolved_model_id = judge.model.id
                judge_response = await run_agent(
                    HarnessRequest.for_prompt(
                        agent=judge,
                        content=json.dumps(
                            {
                                "conversation": self._conversation_context(prior_turns),
                                "question": question,
                                "critiques": [self._result_to_dict(item) for item in successful],
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                judgment = self._parse_judgment(judge_response)
                await self._emit(
                    on_event,
                    {
                        "type": "judgment_status",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "status": "completed",
                        "judgment": asdict(judgment),
                    },
                )
            except Exception as exc:
                judge_failed = True
                await self._emit(
                    on_event,
                    {
                        "type": "judgment_status",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "status": "failed",
                        "error": self._safe_error(exc),
                    },
                )

        if not successful:
            status = "failed"
        elif len(successful) != len(results) or judge_failed:
            status = "partial"
        else:
            status = "completed"
        completed_at = _now_iso()
        turn = CritiqueTurn(
            id=turn_id,
            question=question,
            results=results,
            judgment=judgment,
            status=status,
            created_at=created_at,
            updated_at=completed_at,
        )
        return turn, resolved_model_id

    async def retry_discipline(
        self,
        run_id: str,
        discipline_id: str,
        *,
        turn_id: str | None = None,
        on_event: CritiqueEventCallback | None = None,
    ) -> CritiqueRun:
        current = self.get_run(run_id)
        target_index = next(
            (
                index
                for index, item in enumerate(current.turns)
                if item.id == turn_id
            ),
            len(current.turns) - 1 if turn_id is None else -1,
        )
        if target_index < 0:
            raise CritiqueRunNotFoundError("批判轮次不存在")
        target_turn = current.turns[target_index]
        discipline = next(
            (item for item in current.disciplines if item.id == discipline_id),
            None,
        )
        existing = next(
            (item for item in target_turn.results if item.discipline_id == discipline_id),
            None,
        )
        if discipline is None or existing is None:
            raise CritiqueDisciplineNotFoundError("学科不存在")
        if existing.status != "failed":
            raise ValueError("只能重试失败学科")

        await self._emit(
            on_event,
            {
                "type": "discipline_status",
                "run_id": run_id,
                "turn_id": target_turn.id,
                "discipline_id": discipline_id,
                "status": "running",
            },
        )
        resolved_model_id = current.model_id
        try:
            agent = self._agent_chat_service.bind_prompt_agent(
                agent_id=f"critique-discipline-{discipline.id}",
                name=discipline.name,
                system_prompt=self._discipline_prompt(discipline),
                model_id=current.model_id or None,
            )
            response = await run_agent(
                HarnessRequest.for_prompt(
                    agent=agent,
                    content=self._discipline_request(
                        target_turn.question,
                        current.turns[:target_index],
                    ),
                )
            )
            replacement = CritiqueDisciplineResult(
                discipline_id=discipline.id,
                discipline_name=discipline.name,
                status="completed",
                analysis=self._parse_analysis(response),
            )
            resolved_model_id = agent.model.id
        except Exception as exc:
            replacement = CritiqueDisciplineResult(
                discipline_id=discipline.id,
                discipline_name=discipline.name,
                status="failed",
                error=self._safe_error(exc),
            )
        results = tuple(
            replacement if item.discipline_id == discipline_id else item
            for item in target_turn.results
        )
        await self._emit(
            on_event,
            {
                "type": "discipline_status",
                "run_id": run_id,
                "turn_id": target_turn.id,
                "discipline_id": discipline_id,
                "status": replacement.status,
                "result": self._result_to_dict(replacement),
            },
        )

        successful = tuple(item for item in results if item.status == "completed")
        judgment: CritiqueJudgment | None = None
        judge_failed = False
        if successful:
            await self._emit(
                on_event,
                {
                    "type": "judgment_status",
                    "run_id": run_id,
                    "turn_id": target_turn.id,
                    "status": "running",
                },
            )
            try:
                judge = self._agent_chat_service.bind_prompt_agent(
                    agent_id="critique-judge",
                    name="综合裁判",
                    system_prompt=self._judge_prompt(),
                    model_id=current.model_id or None,
                )
                resolved_model_id = judge.model.id
                response = await run_agent(
                    HarnessRequest.for_prompt(
                        agent=judge,
                        content=json.dumps(
                            {
                                "conversation": self._conversation_context(current.turns[:target_index]),
                                "question": target_turn.question,
                                "critiques": [self._result_to_dict(item) for item in successful],
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                judgment = self._parse_judgment(response)
                await self._emit(
                    on_event,
                    {
                        "type": "judgment_status",
                        "run_id": run_id,
                        "turn_id": target_turn.id,
                        "status": "completed",
                        "judgment": asdict(judgment),
                    },
                )
            except Exception as exc:
                judge_failed = True
                await self._emit(
                    on_event,
                    {
                        "type": "judgment_status",
                        "run_id": run_id,
                        "turn_id": target_turn.id,
                        "status": "failed",
                        "error": self._safe_error(exc),
                    },
                )

        if not successful:
            status = "failed"
        elif len(successful) != len(results) or judge_failed:
            status = "partial"
        else:
            status = "completed"
        updated_turn = CritiqueTurn(
            id=target_turn.id,
            question=target_turn.question,
            results=results,
            judgment=judgment,
            status=status,
            created_at=target_turn.created_at,
            updated_at=_now_iso(),
        )
        turns = tuple(
            updated_turn if index == target_index else item
            for index, item in enumerate(current.turns)
        )
        latest_turn = turns[-1]
        updated = CritiqueRun(
            id=current.id,
            title=current.title,
            question=latest_turn.question,
            model_id=resolved_model_id,
            disciplines=current.disciplines,
            results=latest_turn.results,
            judgment=latest_turn.judgment,
            turns=turns,
            status=latest_turn.status,
            created_at=current.created_at,
            updated_at=updated_turn.updated_at,
        )
        self._write_json(self._run_path(updated.id), self._run_to_dict(updated))
        await self._emit(on_event, {"type": "run_completed", "run": self._run_to_dict(updated)})
        return updated

    def _ensure_unique_name(self, name: str, exclude_id: str | None = None) -> None:
        if not name:
            raise ValueError("学科名称不能为空")
        if any(item.name == name and item.id != exclude_id for item in self.list_disciplines()):
            raise ValueError("学科名称已存在")

    def _discipline_prompt(self, discipline: CritiqueDiscipline) -> str:
        return (
            f"你是{discipline.name}批判者。用户了解范围：{discipline.known_scope}。"
            f"重点批判方向：{discipline.critique_focus}。不要安慰或迎合，直接质疑问题中的假设。"
            "只返回 JSON 对象，必须包含字符串字段 core_assumption、counterevidence、"
            "opportunity_cost、key_question。每个字段使用 30-60 个汉字，直接给出一个核心判断，"
            "不要重复问题或输出其他文字。"
        )

    def _discipline_request(
        self,
        question: str,
        prior_turns: tuple[CritiqueTurn, ...],
    ) -> str:
        if not prior_turns:
            return question
        return json.dumps(
            {
                "conversation": self._conversation_context(prior_turns),
                "question": question,
                "instruction": "结合此前对话继续批判，不要把追问当作孤立的新问题。",
            },
            ensure_ascii=False,
        )

    def _conversation_context(
        self,
        turns: tuple[CritiqueTurn, ...],
    ) -> list[dict[str, object]]:
        return [
            {
                "question": turn.question,
                "critiques": [
                    self._result_to_dict(result)
                    for result in turn.results
                    if result.status == "completed"
                ],
                "judgment": asdict(turn.judgment) if turn.judgment else None,
            }
            for turn in turns
        ]

    @staticmethod
    def _judge_prompt() -> str:
        return (
            "你是多学科综合裁判。比较输入中的成功批判，指出最难回避的问题和学科分歧。"
            "只返回 JSON 对象，必须包含字符串字段 weakest_assumption、"
            "largest_disagreement、recommended_validation。每个字段不超过 100 个汉字，"
            "不要输出其他文字。"
        )

    def _parse_analysis(self, content: str) -> CritiqueAnalysis:
        raw = self._parse_object(content)
        return CritiqueAnalysis(
            core_assumption=self._required_string(raw, "core_assumption"),
            counterevidence=self._required_string(raw, "counterevidence"),
            opportunity_cost=self._required_string(raw, "opportunity_cost"),
            key_question=self._required_string(raw, "key_question"),
        )

    def _parse_judgment(self, content: str) -> CritiqueJudgment:
        raw = self._parse_object(content)
        return CritiqueJudgment(
            weakest_assumption=self._required_string(raw, "weakest_assumption"),
            largest_disagreement=self._required_string(raw, "largest_disagreement"),
            recommended_validation=self._required_string(raw, "recommended_validation"),
        )

    @staticmethod
    def _parse_object(content: str) -> dict[str, object]:
        normalized = content.strip()
        if normalized.startswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                normalized = "\n".join(lines[1:-1])
        try:
            raw = json.loads(normalized)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("模型返回的 JSON 无效") from exc
        if not isinstance(raw, dict):
            raise ValueError("模型返回的 JSON 无效")
        return raw

    @staticmethod
    def _required_string(raw: dict[str, object], key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("模型返回的 JSON 字段不完整")
        return value.strip()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, ValueError):
            return str(exc)
        return "模型调用失败"

    async def _emit(self, callback: CritiqueEventCallback | None, event: dict[str, object]) -> None:
        if callback is not None:
            await callback(event)

    def _data_dir(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def _runs_dir(self) -> Path:
        path = self._data_dir() / "runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _disciplines_path(self) -> Path:
        return self._data_dir() / "disciplines.json"

    def _run_path(self, run_id: str) -> Path:
        if not run_id.startswith("r-") or not run_id[2:].isalnum():
            raise CritiqueRunNotFoundError("批判记录不存在")
        return self._runs_dir() / f"{run_id}.json"

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError):
            return default

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _discipline_from_dict(raw: dict[str, object]) -> CritiqueDiscipline:
        return CritiqueDiscipline(
            id=str(raw["id"]),
            name=str(raw["name"]),
            known_scope=str(raw["known_scope"]),
            critique_focus=str(raw["critique_focus"]),
            default_enabled=bool(raw.get("default_enabled", True)),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )

    @staticmethod
    def _result_to_dict(result: CritiqueDisciplineResult) -> dict[str, object]:
        return {
            "discipline_id": result.discipline_id,
            "discipline_name": result.discipline_name,
            "status": result.status,
            "analysis": asdict(result.analysis) if result.analysis else None,
            "error": result.error,
        }

    def _run_to_dict(self, run: CritiqueRun) -> dict[str, object]:
        return {
            "id": run.id,
            "title": run.title,
            "question": run.question,
            "model_id": run.model_id,
            "disciplines": [asdict(item) for item in run.disciplines],
            "results": [self._result_to_dict(item) for item in run.results],
            "judgment": asdict(run.judgment) if run.judgment else None,
            "turns": [self._turn_to_dict(item) for item in run.turns],
            "status": run.status,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    def _run_from_dict(self, raw: dict[str, object]) -> CritiqueRun:
        results = self._results_from_raw(raw.get("results", []))
        judgment_raw = raw.get("judgment")
        judgment = CritiqueJudgment(**judgment_raw) if isinstance(judgment_raw, dict) else None
        raw_turns = raw.get("turns")
        if isinstance(raw_turns, list) and raw_turns:
            turns = tuple(self._turn_from_dict(item) for item in raw_turns)
        else:
            turns = (
                CritiqueTurn(
                    id=f"t-legacy-{str(raw['id']).removeprefix('r-')}",
                    question=str(raw["question"]),
                    results=results,
                    judgment=judgment,
                    status=str(raw["status"]),
                    created_at=str(raw["created_at"]),
                    updated_at=str(raw["updated_at"]),
                ),
            )
        latest_turn = turns[-1]
        return CritiqueRun(
            id=str(raw["id"]),
            title=str(raw.get("title") or turns[0].question),
            question=latest_turn.question,
            model_id=str(raw.get("model_id", "")),
            disciplines=tuple(self._discipline_from_dict(item) for item in raw.get("disciplines", [])),
            results=latest_turn.results,
            judgment=latest_turn.judgment,
            turns=turns,
            status=latest_turn.status,
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
        )

    def _turn_to_dict(self, turn: CritiqueTurn) -> dict[str, object]:
        return {
            "id": turn.id,
            "question": turn.question,
            "results": [self._result_to_dict(item) for item in turn.results],
            "judgment": asdict(turn.judgment) if turn.judgment else None,
            "status": turn.status,
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
        }

    def _turn_from_dict(self, raw: dict[str, object]) -> CritiqueTurn:
        judgment_raw = raw.get("judgment")
        return CritiqueTurn(
            id=str(raw["id"]),
            question=str(raw["question"]),
            results=self._results_from_raw(raw.get("results", [])),
            judgment=CritiqueJudgment(**judgment_raw) if isinstance(judgment_raw, dict) else None,
            status=str(raw["status"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
        )

    @staticmethod
    def _results_from_raw(raw_results: object) -> tuple[CritiqueDisciplineResult, ...]:
        if not isinstance(raw_results, list):
            return ()
        result_items = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            analysis_raw = item.get("analysis")
            analysis = CritiqueAnalysis(**analysis_raw) if isinstance(analysis_raw, dict) else None
            result_items.append(
                CritiqueDisciplineResult(
                    discipline_id=str(item["discipline_id"]),
                    discipline_name=str(item["discipline_name"]),
                    status=str(item["status"]),
                    analysis=analysis,
                    error=str(item.get("error", "")),
                )
            )
        return tuple(result_items)
