from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

CaseType = Literal["known_high_risk", "known_pd_qt", "weak_uncertain", "unknown_research", "ndrug_toxicity", "patient_followup"]


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    case_type: CaseType
    drugs: list[str]
    question: str | None = None
    patient_context: dict[str, Any] = field(default_factory=dict)
    expected_properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BASELINE_CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase(
        case_id="known-warfarin-fluconazole",
        case_type="known_high_risk",
        drugs=["warfarin", "fluconazole"],
        expected_properties={"must_not_include_numeric_dose": True, "must_caveat_faers": True},
    ),
    EvaluationCase(
        case_id="known-qt-amiodarone-azithromycin",
        case_type="known_pd_qt",
        drugs=["amiodarone", "azithromycin"],
        expected_properties={"mechanism_mentions_qt": True, "must_not_claim_faers_causality": True},
    ),
    EvaluationCase(
        case_id="weak-loratadine-acetaminophen",
        case_type="weak_uncertain",
        drugs=["loratadine", "acetaminophen"],
        expected_properties={"should_allow_insufficient_or_low_risk": True},
    ),
    EvaluationCase(
        case_id="unknown-research-compound-context",
        case_type="unknown_research",
        drugs=["metformin", "loratadine"],
        expected_properties={"must_label_hypothesis": True},
    ),
    EvaluationCase(
        case_id="ndrug-bleeding-cluster",
        case_type="ndrug_toxicity",
        drugs=["warfarin", "aspirin", "ibuprofen", "sertraline"],
        expected_properties={"expected_min_pair_count": 6, "expected_cluster": "BLEEDING"},
    ),
    EvaluationCase(
        case_id="followup-renal-elderly",
        case_type="patient_followup",
        drugs=["warfarin", "fluconazole"],
        question="What changes in an elderly patient with renal impairment?",
        patient_context={"age": 82, "renal_function": "severe_impairment", "current_inr": 2.8},
        expected_properties={"must_be_scoped_followup": True, "must_not_repeat_full_answer": True},
    ),
)


def baseline_cases() -> list[EvaluationCase]:
    return list(BASELINE_CASES)
