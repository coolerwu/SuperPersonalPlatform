from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ApprovalDecisionType = Literal["approve", "reject"]


@dataclass(frozen=True)
class RunApprovalAction:
    name: str
    args: dict[str, Any]
    description: str
    allowed_decisions: tuple[ApprovalDecisionType, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "description": self.description,
            "allowed_decisions": list(self.allowed_decisions),
        }

    @classmethod
    def from_json(cls, raw: Any) -> RunApprovalAction:
        data = raw if isinstance(raw, dict) else {}
        raw_decisions = data.get("allowed_decisions")
        decisions = tuple(
            decision
            for item in (raw_decisions if isinstance(raw_decisions, list | tuple) else ())
            if (decision := str(item).strip()) in {"approve", "reject"}
        )
        return cls(
            name=str(data.get("name") or ""),
            args=dict(data.get("args") or {}) if isinstance(data.get("args"), dict) else {},
            description=str(data.get("description") or ""),
            allowed_decisions=decisions or ("approve", "reject"),
        )


@dataclass(frozen=True)
class RunApprovalInterrupt:
    interrupt_id: str
    actions: tuple[RunApprovalAction, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "interrupt_id": self.interrupt_id,
            "actions": [action.to_json() for action in self.actions],
        }

    @classmethod
    def from_json(cls, raw: Any) -> RunApprovalInterrupt:
        data = raw if isinstance(raw, dict) else {}
        raw_actions = data.get("actions")
        actions = tuple(
            RunApprovalAction.from_json(item)
            for item in (raw_actions if isinstance(raw_actions, list | tuple) else ())
        )
        return cls(interrupt_id=str(data.get("interrupt_id") or ""), actions=actions)


@dataclass(frozen=True)
class RunApprovalRequest:
    interrupts: tuple[RunApprovalInterrupt, ...]

    def to_json(self) -> dict[str, Any]:
        return {"interrupts": [item.to_json() for item in self.interrupts]}

    @classmethod
    def from_json(cls, raw: Any) -> RunApprovalRequest:
        data = raw if isinstance(raw, dict) else {}
        raw_interrupts = data.get("interrupts")
        interrupts = tuple(
            RunApprovalInterrupt.from_json(item)
            for item in (raw_interrupts if isinstance(raw_interrupts, list | tuple) else ())
        )
        return cls(interrupts=tuple(item for item in interrupts if item.interrupt_id and item.actions))


@dataclass(frozen=True)
class RunApprovalDecision:
    type: ApprovalDecisionType
    message: str = ""

    def to_json(self) -> dict[str, str]:
        payload = {"type": self.type}
        if self.message:
            payload["message"] = self.message
        return payload


@dataclass(frozen=True)
class RunApprovalResume:
    values: tuple[tuple[str, tuple[RunApprovalDecision, ...]], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "values": [
                {
                    "interrupt_id": interrupt_id,
                    "decisions": [decision.to_json() for decision in decisions],
                }
                for interrupt_id, decisions in self.values
            ]
        }

    def to_command_value(self) -> dict[str, Any]:
        return {
            interrupt_id: {"decisions": [decision.to_json() for decision in decisions]}
            for interrupt_id, decisions in self.values
        }

    @classmethod
    def from_json(cls, raw: Any) -> RunApprovalResume:
        data = raw if isinstance(raw, dict) else {}
        raw_values = data.get("values")
        values: list[tuple[str, tuple[RunApprovalDecision, ...]]] = []
        for item in raw_values if isinstance(raw_values, list | tuple) else ():
            entry = item if isinstance(item, dict) else {}
            interrupt_id = str(entry.get("interrupt_id") or "").strip()
            decisions: list[RunApprovalDecision] = []
            for raw_decision in entry.get("decisions") if isinstance(entry.get("decisions"), list | tuple) else ():
                decision_data = raw_decision if isinstance(raw_decision, dict) else {}
                decision_type = str(decision_data.get("type") or "").strip()
                if decision_type not in {"approve", "reject"}:
                    continue
                decisions.append(
                    RunApprovalDecision(
                        type=decision_type,
                        message=str(decision_data.get("message") or ""),
                    )
                )
            if interrupt_id and decisions:
                values.append((interrupt_id, tuple(decisions)))
        return cls(values=tuple(values))
