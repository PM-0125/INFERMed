from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import re

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
    status: str = "review_required"
    claims: list[dict[str, Any]] = field(default_factory=list)

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
        contains_dose_language = any(
            term in text
            for term in ("reduce the dose", "dose reduction", "increase the dose")
        )
        notes = [
            "Deterministic grounding verifier.",
            "A model-based critic can be added after decision/evidence schemas stabilize.",
        ]
        if not evidence_cards:
            notes.append("No evidence cards were available for grounding.")
        claims = _check_claims(answer_text, decision, evidence_cards)
        requires_review = (
            safety_report.requires_review
            or not claims
            or any(claim["status"] != "supported" for claim in claims)
        )
        notes.append(
            "Support checks are narrow text/reference checks, not clinical validation. Paraphrases remain unresolved."
        )
        return GroundingVerification(
            checked=True,
            risk_level=decision.risk_level,
            contains_dose_language=contains_dose_language,
            safety_requires_review=safety_report.requires_review,
            safety_findings=[finding.to_dict() for finding in safety_report.findings],
            evidence_card_count=len(evidence_cards),
            notes=notes,
            status="review_required" if requires_review else "checks_passed",
            claims=claims,
        )


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").strip()).lower().rstrip(".")


def _check_claims(text, decision, cards):
    by_id = {card.evidence_id: card for card in cards}
    checks = []
    # Keep trailing citations attached to their sentence and never treat a
    # paragraph citation as support for an unrelated paragraph.
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?!\[ev_)", line):
            refs = re.findall(r"\bev_[A-Za-z0-9_]+\b", sentence)
            plain = re.sub(r"\[[^\]]*\bev_[^\]]*\]", "", sentence).strip().lstrip("- ")
            if not plain:
                continue
            reasons = []
            status = "unresolved"
            cited = [by_id[ref] for ref in refs if ref in by_id]
            mentioned = [
                drug
                for drug in decision.drugs
                if re.search(r"(?<!\w)" + re.escape(drug) + r"(?!\w)", plain, re.I)
            ]
            scope = mentioned or decision.drugs
            if any(ref not in by_id for ref in refs):
                status = "unsupported"
                reasons.append("Unknown evidence reference.")
            elif cited and any(
                not set(scope).issubset(set(card.drug_scope)) for card in cited
            ):
                status = "unsupported"
                reasons.append("Citation does not cover the claimed medication scope.")
            elif cited and any(
                _normalized(plain) == _normalized(card.claim_text) for card in cited
            ):
                status = "supported"
                reasons.append(
                    "Exact cited evidence statement; not independent clinical validation."
                )
            else:
                reasons.append(
                    "Semantic support is unresolved; a citation or keyword alone does not verify a claim."
                )
            if decision.risk_level in {
                "major",
                "avoid",
                "contraindicated",
            } and re.search(
                r"\b(?:no interaction|safe to combine|no monitoring (?:is )?needed)\b",
                plain,
                re.I,
            ):
                status = "contradicted"
                reasons.append("Statement conflicts with the structured risk decision.")
            if (
                re.search(
                    r"\b(?:dose|dosage|reduce|increase|monitor(?:ing)?|checks?|repeat(?:ed)?|every)\b",
                    plain,
                    re.I,
                )
                and re.search(r"\d", plain)
                and status != "supported"
            ):
                status = "unsupported"
                reasons.append(
                    "Quantitative action is not an exact scoped statement from the cited evidence."
                )
            checks.append(
                {
                    "text": plain,
                    "evidence_ids": refs,
                    "drug_scope": scope,
                    "status": status,
                    "reasons": reasons,
                }
            )
    return checks
