from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SafetySeverity = Literal["info", "low", "medium", "high", "critical"]
SafetyFindingType = Literal[
    "unsupported_dose_guidance",
    "causality_overclaim",
    "unsupported_patient_specific_action",
    "unscoped_hypothesis",
    "missing_evidence_disclosure",
    "missing_source_caveat",
    "unsupported_claim",
]


@dataclass(frozen=True)
class SafetyFinding:
    finding_type: SafetyFindingType
    severity: SafetySeverity
    message: str
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SafetyReport:
    analysis_id: str
    allow_generation: bool
    requires_review: bool
    findings: list[SafetyFinding] = field(default_factory=list)
    guardrail_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
