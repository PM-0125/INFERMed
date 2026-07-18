from __future__ import annotations

import re

from src.domain.evidence.entities import EvidenceCard
from src.domain.reasoning.entities import InteractionReasoningRecord
from src.domain.safety.entities import SafetyFinding, SafetyReport

_DOSE_LANGUAGE_RE = re.compile(
    r"\b(?:dose|dosing|dosage|reduce|reduction|increase|adjust|adjustment)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\b\d{1,3}\s*(?:-|to)?\s*\d{0,3}\s*%")
_CAUSAL_OVERCLAIM_RE = re.compile(
    r"\b(?:faers|prr|twosides|offsides|spontaneous reports?)\b.{0,80}"
    r"\b(?:proves?|confirms?|establishes?|demonstrates?)\b",
    re.IGNORECASE | re.DOTALL,
)
_PATIENT_SPECIFIC_RE = re.compile(
    r"\b(?:must stop|must discontinue|contraindicated|do not use|never use)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:hypothesis|plausible|uncertain|limited|not established|not proven|insufficient|missing|not found)\b",
    re.IGNORECASE,
)


class ZeroTrustSafetyGate:
    """Deterministic safety review for model-written explanations.

    The gate is intentionally conservative: it does not prove an answer is
    clinically correct. It detects common product-risk failures before the UI
    presents an explanation as evidence-backed.
    """

    def evaluate(
        self,
        *,
        analysis_id: str,
        answer_text: str,
        reasoning_record: InteractionReasoningRecord,
        evidence_cards: list[EvidenceCard],
    ) -> SafetyReport:
        text = answer_text or ""
        findings: list[SafetyFinding] = []

        if _has_quantitative_dose_guidance(text) and not _has_dose_evidence(evidence_cards):
            findings.append(
                SafetyFinding(
                    finding_type="unsupported_dose_guidance",
                    severity="high",
                    message=(
                        "The explanation contains quantitative or directive dose language, "
                        "but no dose-specific source card supports it."
                    ),
                )
            )

        if _CAUSAL_OVERCLAIM_RE.search(text):
            findings.append(
                SafetyFinding(
                    finding_type="causality_overclaim",
                    severity="high",
                    message="Associative pharmacovigilance evidence is phrased as causal proof.",
                )
            )

        if _PATIENT_SPECIFIC_RE.search(text) and reasoning_record.known_status != "known_direct":
            findings.append(
                SafetyFinding(
                    finding_type="unsupported_patient_specific_action",
                    severity="medium",
                    message=(
                        "The explanation uses patient-directive language without direct "
                        "known-interaction support in the reasoning record."
                    ),
                )
            )

        if reasoning_record.known_status.startswith("unknown") and not _UNCERTAINTY_RE.search(text):
            findings.append(
                SafetyFinding(
                    finding_type="unscoped_hypothesis",
                    severity="medium",
                    message="Unknown-combination reasoning must be explicitly scoped as uncertain or hypothesis-level.",
                )
            )

        if reasoning_record.missing_profile_elements and not _UNCERTAINTY_RE.search(text):
            findings.append(
                SafetyFinding(
                    finding_type="missing_evidence_disclosure",
                    severity="low",
                    message="The explanation does not disclose missing profile elements.",
                    payload={"missing_profile_elements": reasoning_record.missing_profile_elements},
                )
            )

        if _mentions_association_source(text) and not _contains_association_caveat(text):
            findings.append(
                SafetyFinding(
                    finding_type="missing_source_caveat",
                    severity="medium",
                    message="Associative evidence sources are mentioned without an association-versus-causality caveat.",
                )
            )

        unsupported = _unsupported_named_claims(text, evidence_cards)
        if unsupported:
            findings.append(
                SafetyFinding(
                    finding_type="unsupported_claim",
                    severity="medium",
                    message="The explanation contains source-like claims that are weakly represented in evidence cards.",
                    payload={"claims": unsupported[:8]},
                )
            )

        return SafetyReport(
            analysis_id=analysis_id,
            allow_generation=not any(item.severity in {"critical"} for item in findings),
            requires_review=any(item.severity in {"medium", "high", "critical"} for item in findings),
            findings=findings,
            guardrail_notes=[
                "Dose guidance requires direct dose-specific evidence.",
                "FAERS, TWOSIDES, OFFSIDES, and PRR signals are associative unless supported by stronger sources.",
                "Unknown combinations must remain hypothesis-level unless direct evidence is present.",
            ],
        )


def _has_quantitative_dose_guidance(text: str) -> bool:
    return bool(_DOSE_LANGUAGE_RE.search(text) and _PERCENT_RE.search(text))


def _has_dose_evidence(cards: list[EvidenceCard]) -> bool:
    strong_grades = {"label", "guideline", "clinical_study", "pk_study"}
    for card in cards:
        searchable = f"{card.claim_type} {card.claim_text}".lower()
        if card.evidence_grade in strong_grades and any(term in searchable for term in ("dose", "dosing", "dosage")):
            return True
    return False


def _mentions_association_source(text: str) -> bool:
    return bool(re.search(r"\b(?:faers|prr|twosides|offsides|nsides|spontaneous reports?)\b", text, re.IGNORECASE))


def _contains_association_caveat(text: str) -> bool:
    return bool(re.search(r"\b(?:associative|association|not causal|not causality|does not prove|signal only|reporting)\b", text, re.IGNORECASE))


def _unsupported_named_claims(text: str, cards: list[EvidenceCard]) -> list[str]:
    evidence_text = " ".join(f"{card.source_name} {card.claim_type} {card.claim_text}" for card in cards).lower()
    claims: list[str] = []
    for term in ("guideline", "clinical trial", "boxed warning", "contraindicated", "drug label", "pharmacogenomic"):
        if term in text.lower() and term not in evidence_text:
            claims.append(term)
    return claims
