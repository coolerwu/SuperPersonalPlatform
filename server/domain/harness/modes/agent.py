from dataclasses import dataclass, replace
from enum import StrEnum
import json

from server.domain.harness.contracts import (
    Agent,
    AgentModelRunner,
    AgentRunBlockedError,
    AgentRunFailedError,
    AgentToolCall,
    AgentToolResult,
    AgentVerifier,
    ChatOptions,
    CheckpointEmitter,
    EvidenceRecord,
    GoalContract,
    HarnessRequest,
    OutputCandidate,
    RawToolResult,
    VerificationResult,
)


class AgentRunPhase(StrEnum):
    GOAL = "goal"
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"
    VERIFY = "verify"
    FINALIZE = "finalize"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentRunState:
    phase: AgentRunPhase = AgentRunPhase.GOAL
    turn: int = 0
    goal: GoalContract | None = None
    messages: tuple[object, ...] = ()
    pending_tool_calls: tuple[AgentToolCall, ...] = ()
    raw_tool_results: tuple[RawToolResult, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    candidate: OutputCandidate | None = None
    verification: VerificationResult | None = None
    final_response: str = ""


class LLMVerifier:
    def __init__(self, model_runner: AgentModelRunner) -> None:
        self._model_runner = model_runner

    async def verify(
        self,
        agent: Agent,
        goal: GoalContract,
        evidence: tuple[EvidenceRecord, ...],
        candidate: OutputCandidate,
    ) -> VerificationResult:
        payload = {
            "goal": goal.goal,
            "completion_criteria": goal.completion_criteria,
            "required_evidence": goal.required_evidence,
            "evidence": [record.__dict__ for record in evidence],
            "candidate": candidate.content,
        }
        response = await self._model_runner.complete(
            (
                "你是独立验证器，不是任务执行者。严格判断候选输出是否满足全部目标、"
                "完成条件且被证据支持。只返回 JSON："
                '{"passed":bool,"blocked":bool,"feedback":"string"}。'
            ),
            json.dumps(payload, ensure_ascii=False),
            (),
        )
        data = _parse_json_object(response, "verification")
        return VerificationResult(
            passed=bool(data.get("passed")),
            blocked=bool(data.get("blocked")),
            feedback=str(data.get("feedback") or ""),
        )


class AgentRunner:
    def __init__(
        self,
        model_runner: AgentModelRunner,
        verifier: AgentVerifier,
    ) -> None:
        self._model_runner = model_runner
        self._verifier = verifier

    async def run(
        self,
        agent: Agent,
        request: HarnessRequest,
        options: ChatOptions,
        emit: CheckpointEmitter,
    ) -> str:
        _validate_agent_request(request)
        state = AgentRunState()

        while state.phase not in {
            AgentRunPhase.COMPLETED,
            AgentRunPhase.FAILED,
            AgentRunPhase.BLOCKED,
            AgentRunPhase.CANCELLED,
        }:
            if state.phase is AgentRunPhase.GOAL:
                state = await self._goal(agent, request, state, emit)
            elif state.phase is AgentRunPhase.REASON:
                state = await self._reason(agent, request, options, state, emit)
            elif state.phase is AgentRunPhase.ACT:
                state = await self._act(request, state, emit)
            elif state.phase is AgentRunPhase.OBSERVE:
                state = self._observe(state)
                await emit("observe", "工具结果已写入证据账本", "")
            elif state.phase is AgentRunPhase.VERIFY:
                state = await self._verify(agent, options, state, emit)
            elif state.phase is AgentRunPhase.FINALIZE:
                state = await self._finalize(agent, state, emit)

        if state.phase is AgentRunPhase.FAILED:
            feedback = state.verification.feedback if state.verification else "Agent run failed"
            await emit("failed", "任务失败", feedback)
            raise AgentRunFailedError(feedback)
        if state.phase is AgentRunPhase.BLOCKED:
            feedback = state.verification.feedback if state.verification else "Agent run blocked"
            await emit("blocked", "任务阻塞", feedback)
            raise AgentRunBlockedError(feedback)
        if state.phase is AgentRunPhase.CANCELLED:
            await emit("cancelled", "任务已取消", "")
            raise AgentRunFailedError("Agent run cancelled")
        return state.final_response

    async def _goal(
        self,
        agent: Agent,
        request: HarnessRequest,
        state: AgentRunState,
        emit: CheckpointEmitter,
    ) -> AgentRunState:
        await emit("goal", "生成目标契约", "")
        response = await self._model_runner.complete(
            (
                "把用户任务转换为严格目标契约。只返回 JSON："
                '{"goal":"string","completion_criteria":["string"],'
                '"output_format":"string","required_evidence":["tool_name"]}。'
                "required_evidence 只能填写 available_tools 中的准确工具名；"
                "不需要工具证据时返回空数组。"
            ),
            json.dumps(
                {
                    "task": request.content,
                    "available_tools": request.tool_names,
                },
                ensure_ascii=False,
            ),
            request.images,
        )
        data = _parse_json_object(response, "goal")
        goal = GoalContract(
            goal=str(data.get("goal") or "").strip(),
            completion_criteria=_string_tuple(data.get("completion_criteria")),
            output_format=str(data.get("output_format") or "plain text").strip(),
            required_evidence=_string_tuple(data.get("required_evidence")),
        )
        if not goal.goal or not goal.completion_criteria:
            raise AgentRunFailedError("goal contract is incomplete")
        return replace(state, phase=AgentRunPhase.REASON, goal=goal)

    async def _reason(
        self,
        agent: Agent,
        request: HarnessRequest,
        options: ChatOptions,
        state: AgentRunState,
        emit: CheckpointEmitter,
    ) -> AgentRunState:
        assert state.goal is not None
        next_turn = state.turn + 1
        await emit("reason", "推理下一步", f"第 {next_turn} 轮")
        context = {
            "goal": state.goal.__dict__,
            "evidence": [record.__dict__ for record in state.evidence],
            "verification_feedback": (
                state.verification.feedback if state.verification else ""
            ),
        }
        result = await self._model_runner.reason_with_tools(
            (
                f"{agent.definition.system_prompt}\n\n"
                "严格执行目标契约。需要证据时调用工具；证据充分后直接给出候选输出。"
            ),
            f"用户任务：{request.content}\n运行上下文：{json.dumps(context, ensure_ascii=False)}",
            request.tool_names,
            state.messages,
            request.images,
        )
        if result.tool_calls:
            await emit(
                "reason",
                "模型请求工具",
                ", ".join(call.name for call in result.tool_calls),
            )
            return replace(
                state,
                phase=AgentRunPhase.ACT,
                turn=next_turn,
                messages=result.messages,
                pending_tool_calls=result.tool_calls,
                candidate=None,
            )
        if result.content.strip():
            return replace(
                state,
                phase=AgentRunPhase.VERIFY,
                turn=next_turn,
                messages=result.messages,
                candidate=OutputCandidate(result.content.strip()),
            )
        return replace(
            state,
            phase=(
                AgentRunPhase.FAILED
                if next_turn >= options.max_iterations
                else AgentRunPhase.REASON
            ),
            turn=next_turn,
            messages=result.messages,
            verification=VerificationResult(False, False, "model produced no candidate"),
        )

    async def _act(
        self,
        request: HarnessRequest,
        state: AgentRunState,
        emit: CheckpointEmitter,
    ) -> AgentRunState:
        tool_registry = request.tool_registry
        if tool_registry is None:
            raise ValueError("agent mode requires tool_registry")
        results = []
        for call in state.pending_tool_calls:
            await emit("act", f"执行工具 {call.name}", _tool_checkpoint_detail(call))
            try:
                content = await tool_registry.dispatch(
                    call.name, call.args, request.tool_runtime
                )
                ok = True
            except Exception as exc:
                content = f"ERROR: {exc}"
                ok = False
            await emit("act", f"工具完成 {call.name}", "")
            results.append(RawToolResult(call.id, call.name, content, ok))
        return replace(
            state,
            phase=AgentRunPhase.OBSERVE,
            raw_tool_results=tuple(results),
            pending_tool_calls=(),
        )

    def _observe(self, state: AgentRunState) -> AgentRunState:
        records = tuple(
            EvidenceRecord(
                source=result.tool_name,
                content=_clean_tool_result(result.content),
                ok=result.ok,
            )
            for result in state.raw_tool_results
        )
        tool_messages = tuple(
            AgentToolResult(
                tool_call_id=result.tool_call_id,
                content=_clean_tool_result(result.content),
            )
            for result in state.raw_tool_results
        )
        return replace(
            state,
            phase=AgentRunPhase.REASON,
            messages=self._model_runner.append_tool_results(state.messages, tool_messages),
            raw_tool_results=(),
            evidence=(*state.evidence, *records),
        )

    async def _verify(
        self,
        agent: Agent,
        options: ChatOptions,
        state: AgentRunState,
        emit: CheckpointEmitter,
    ) -> AgentRunState:
        assert state.goal is not None and state.candidate is not None
        await emit("verify", "验证候选输出", "")
        missing = _missing_evidence(state.goal, state.evidence)
        if missing:
            verification = VerificationResult(
                False,
                False,
                f"missing required evidence: {', '.join(missing)}",
            )
        else:
            verification = await self._verifier.verify(
                agent, state.goal, state.evidence, state.candidate
            )
        if verification.passed:
            return replace(
                state,
                phase=AgentRunPhase.FINALIZE,
                verification=verification,
            )
        if verification.blocked:
            return replace(
                state,
                phase=AgentRunPhase.BLOCKED,
                verification=verification,
            )
        return replace(
            state,
            phase=(
                AgentRunPhase.FAILED
                if state.turn >= options.max_iterations
                else AgentRunPhase.REASON
            ),
            verification=verification,
            candidate=None,
        )

    async def _finalize(
        self,
        agent: Agent,
        state: AgentRunState,
        emit: CheckpointEmitter,
    ) -> AgentRunState:
        assert state.goal is not None and state.candidate is not None
        await emit("finalize", "生成最终回复", "")
        payload = {
            "goal": state.goal.goal,
            "output_format": state.goal.output_format,
            "candidate": state.candidate.content,
            "evidence": [record.__dict__ for record in state.evidence],
        }
        response = await self._model_runner.complete(
            "候选输出已经通过验证。只做格式化和总结，不新增未经证据支持的结论。",
            json.dumps(payload, ensure_ascii=False),
            (),
        )
        await emit("completed", "任务已完成", "")
        return replace(
            state,
            phase=AgentRunPhase.COMPLETED,
            final_response=response,
        )


def _validate_agent_request(request: HarnessRequest) -> None:
    if request.tool_names and request.tool_registry is None:
        raise ValueError("agent mode requires tool_registry")


def _parse_json_object(content: str, label: str) -> dict[str, object]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise AgentRunFailedError(f"{label} response is not valid JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentRunFailedError(f"{label} response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise AgentRunFailedError(f"{label} response must be a JSON object")
    return data


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _clean_tool_result(content: str) -> str:
    return content.strip()[:12000]


def _missing_evidence(
    goal: GoalContract,
    evidence: tuple[EvidenceRecord, ...],
) -> tuple[str, ...]:
    searchable = "\n".join(
        f"{record.source}\n{record.content}" for record in evidence if record.ok
    ).lower()
    return tuple(item for item in goal.required_evidence if item.lower() not in searchable)


def _tool_checkpoint_detail(tool_call: AgentToolCall) -> str:
    if tool_call.name == "read_skill":
        return str(tool_call.args.get("id") or "")
    return ""
