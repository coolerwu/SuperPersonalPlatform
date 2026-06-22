from dataclasses import dataclass


class CritiqueDisciplineNotFoundError(ValueError):
    pass


class CritiqueRunNotFoundError(ValueError):
    pass


@dataclass(frozen=True)
class CritiqueDiscipline:
    id: str
    name: str
    known_scope: str
    critique_focus: str
    default_enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("学科 ID 不能为空")
        if not self.name.strip():
            raise ValueError("学科名称不能为空")
        if not self.known_scope.strip():
            raise ValueError("了解范围不能为空")
        if not self.critique_focus.strip():
            raise ValueError("重点批判方向不能为空")


@dataclass(frozen=True)
class CritiqueAnalysis:
    core_assumption: str
    counterevidence: str
    opportunity_cost: str
    key_question: str


@dataclass(frozen=True)
class CritiqueDisciplineResult:
    discipline_id: str
    discipline_name: str
    status: str
    analysis: CritiqueAnalysis | None = None
    error: str = ""


@dataclass(frozen=True)
class CritiqueJudgment:
    weakest_assumption: str
    largest_disagreement: str
    recommended_validation: str


@dataclass(frozen=True)
class CritiqueTurn:
    id: str
    question: str
    results: tuple[CritiqueDisciplineResult, ...]
    judgment: CritiqueJudgment | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CritiqueRun:
    id: str
    title: str
    question: str
    model_id: str
    disciplines: tuple[CritiqueDiscipline, ...]
    results: tuple[CritiqueDisciplineResult, ...]
    judgment: CritiqueJudgment | None
    turns: tuple[CritiqueTurn, ...]
    status: str
    created_at: str
    updated_at: str
