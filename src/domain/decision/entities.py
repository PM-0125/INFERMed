from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RiskLevel = Literal[
    "contraindicated",
    "avoid",
    "major",
    "moderate",
    "minor",
    "theoretical",
    "no_clinically_significant_interaction_found",
    "insufficient_evidence",
]

EvidenceGrade = Literal[
    "label",
    "guideline",
    "clinical_study",
    "pk_study",
    "mechanistic",
    "pharmacovigilance_signal",
    "inferred_hypothesis",
    "insufficient",
]

ClinicalAction = Literal[
    "avoid",
    "consider_alternative",
    "dose_adjust",
    "monitor",
    "counsel",
    "no_action",
    "specialist_review",
    "insufficient_evidence",
]


@dataclass(frozen=True)
class InteractionDecision:
    analysis_id: str
    interaction_scope: str
    drugs: list[str]
    risk_level: RiskLevel
    confidence: str
    evidence_grade: EvidenceGrade
    mechanisms: list[dict[str, Any]] = field(default_factory=list)
    patient_amplifiers: list[str] = field(default_factory=list)
    recommended_action: ClinicalAction = "monitor"
    monitoring: list[str] = field(default_factory=list)
    missing_patient_factors: list[str] = field(default_factory=list)
    what_would_change_the_answer: list[str] = field(default_factory=list)
    source_limitations: list[str] = field(default_factory=list)
    abstention_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

