from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

KnownStatus = Literal[
    "known_direct",
    "known_signal_supported",
    "unknown_mechanistically_plausible",
    "unknown_insufficient_evidence",
]

ReasoningCategory = Literal[
    "known_pair",
    "pk_overlap",
    "pd_overlap",
    "toxicity_convergence",
    "adverse_event_signal",
    "experimental_combination",
    "literature_support",
    "missing_evidence",
]

SupportLevel = Literal["established", "supported", "plausible", "weak", "insufficient"]


@dataclass(frozen=True)
class ReasoningSignal:
    category: ReasoningCategory
    label: str
    support_level: SupportLevel
    drug_scope: list[str]
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InteractionHypothesis:
    hypothesis_id: str
    mechanism_type: str
    statement: str
    support_level: SupportLevel
    affected_drugs: list[str]
    drivers: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InteractionReasoningRecord:
    analysis_id: str
    drugs: list[str]
    known_status: KnownStatus
    signals: list[ReasoningSignal] = field(default_factory=list)
    hypotheses: list[InteractionHypothesis] = field(default_factory=list)
    missing_profile_elements: dict[str, list[str]] = field(default_factory=dict)
    uncertainty_factors: list[str] = field(default_factory=list)
    required_next_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

