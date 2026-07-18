from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.domain.decision.entities import InteractionDecision
from src.domain.evidence.entities import EvidenceCard
from src.domain.safety.entities import SafetyReport


@dataclass(frozen=True)
class GroundingVerification:
    checked: bool
    risk_level: str
    contains_dose_language: bool
    safety_requires_review: bool
    safety_findings: list[dict[str, Any]] = field(default_factory=list)
    evidence_card_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExplanationVerifier:
    def verify(
        self,
        *,
        answer_text: str,
        decision: InteractionDecision,
        evidence_cards: list[EvidenceCard],
        safety_report: SafetyReport,
    ) -> GroundingVerification:
        text = (answer_text or "").lower()
        contains_dose_language = any(term in text for term in ("reduce the dose", "dose reduction", "increase the dose"))
        notes = [
            "Deterministic grounding verifier.",
            "A model-based critic can be added after decision/evidence schemas stabilize.",
        ]
        if not evidence_cards:
            notes.append("No evidence cards were available for grounding.")
        return GroundingVerification(
            checked=True,
            risk_level=decision.risk_level,
            contains_dose_language=contains_dose_language,
            safety_requires_review=safety_report.requires_review,
            safety_findings=[finding.to_dict() for finding in safety_report.findings],
            evidence_card_count=len(evidence_cards),
            notes=notes,
        )
