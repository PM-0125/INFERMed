from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import re
from src.domain.explanation.claim_types import claim_kind
from src.domain.explanation.meaning import verify_meaning
from src.domain.explanation.applicability import assess_applicability
from src.domain.explanation.patient_facts import patient_fact_lines

from src.domain.explanation.passages import (
    match_sources,
    plain as claim_plain,
    units,
    partial_source_quotes,
    fda_strength_conflicts,
)
from src.domain.explanation.action_support import (
    _action_candidate,
    WITHHELD,
    REVIEW_FAILED,
    PATIENT_WITHHELD,
    INTERPRETATION_WITHHELD,
)

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
        patient_context=None,
    ) -> GroundingVerification:
        text = (answer_text or "").lower()
        contains_dose_language = any(
            term in text
            for term in ("reduce the dose", "dose reduction", "increase the dose")
        )
        notes = [
            "Deterministic grounding verifier.",
            "Model revision, when present, is recorded separately and does not establish clinical correctness.",
        ]
        if not evidence_cards:
            notes.append("No evidence cards were available for grounding.")
        claims = _check_claims(
            answer_text, decision, evidence_cards, patient_context=patient_context
        )
        requires_review = (
            safety_report.requires_review
            or not claims
            or any(claim["status"] != "supported" for claim in claims)
            or any(
                claim["claim_kind"] == "prescribing_advice"
                and claim["patient_applicability"]["status"]
                in {"missing_context", "condition_conflict"}
                for claim in claims
            )
        )
        notes.append(
            "Source passage matches verify text and medication scope, not clinical appropriateness. Unmatched interpretations require review."
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


def _check_claims(text, decision, cards, patient_context=None):
    by_id = {card.evidence_id: card for card in cards}
    checks = []
    patient_lines = {r["text"]: r for r in patient_fact_lines(patient_context)}
    # Keep trailing citations attached to their sentence and never treat a
    # paragraph citation as support for an unrelated paragraph.
    parent_scope = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cleaned = claim_plain(line)
        if cleaned in patient_lines:
            fact = patient_lines[cleaned]
            checks.append(
                {
                    "text": cleaned,
                    "knowledge_basis": "supplied_patient_context",
                    "claim_kind": "patient_input",
                    "status": "supported",
                    "verification_method": "literal_patient_field",
                    "source_matches": [],
                    "patient_applicability": assess_applicability("", patient_context),
                    "evidence_ids": [],
                    "drug_scope": [],
                    "publication_state": "published",
                    "reasons": [
                        f'Literal supplied field {fact["field"]}; no clinical interpretation.'
                    ],
                }
            )
            continue
        actor = re.fullmatch(r"Actor:\s*(.+)", cleaned, re.I)
        if actor:
            parent_scope = [
                d for d in decision.drugs if d.lower() == actor[1].strip().lower()
            ]
            continue
        if line and not line[0].isspace():
            parent_scope = []
        if cleaned in {
            WITHHELD,
            REVIEW_FAILED,
            PATIENT_WITHHELD,
            INTERPRETATION_WITHHELD,
        } or cleaned.startswith(("Disclaimer:", "Use alongside clinical judgment")):
            continue
        if re.fullmatch(r"[\d.)\s]*", cleaned) or (
            "**" in line and re.fullmatch(r"[\w ]+", cleaned)
        ):
            continue
        # Exact multi-sentence source quotations must keep adjacent qualifiers.
        refs_on_line = re.findall(r"\bev_[A-Za-z0-9_]+\b", line)
        cited_on_line = [by_id[r] for r in refs_on_line if r in by_id]
        whole_matches = match_sources(
            cleaned,
            cited_on_line,
            parent_scope
            or [
                d
                for d in decision.drugs
                if re.search(r"(?<!\w)" + re.escape(d) + r"(?!\w)", cleaned, re.I)
            ]
            or decision.drugs,
        )
        for sentence in ([line] if whole_matches else units(line)):
            refs = re.findall(r"\bev_[A-Za-z0-9_]+\b", sentence)
            plain = claim_plain(sentence)
            if not plain:
                continue
            reasons = []
            status = "unresolved"
            cited = [by_id[ref] for ref in refs if ref in by_id]
            mentioned = [
                drug
                for drug in decision.drugs
                if re.search(r"(?<!\w)" + re.escape(drug) + r"(?!\w)", sentence, re.I)
            ]
            scope = mentioned or parent_scope or decision.drugs
            matches = match_sources(plain, cited, scope)
            fragments = partial_source_quotes(sentence, cited, scope)
            method = "exact_source_text" if matches else "unresolved"
            if any(ref not in by_id for ref in refs):
                status = "unsupported"
                reasons.append("Unknown evidence reference.")
            elif (
                not matches
                and not fragments
                and cited
                and not set(scope).issubset(
                    {drug for card in cited for drug in card.drug_scope}
                )
            ):
                status = "unsupported"
                reasons.append("Citation does not cover the claimed medication scope.")
            elif matches:
                status = "supported"
                reasons.append(
                    "Complete source statement matched with medication scope; clinical applicability still requires review."
                )
            else:
                if fragments:
                    status = "partial"
                    matches = fragments
                    reasons.append(
                        "Quoted source fragments matched; omissions and surrounding interpretation are not verified. This is not support for a recommendation."
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
                _action_candidate(plain, decision.drugs)
                and re.search(r"(?<![\w])\d", plain)
                and status not in {"supported", "partial"}
            ):
                status = "unsupported"
                reasons.append(
                    "Quantitative action is not an exact scoped statement from the cited evidence."
                )
            conflicts = fda_strength_conflicts(plain, cited)
            semantic = verify_meaning(sentence, cited, scope)
            if semantic and not any(ref not in by_id for ref in refs):
                status, method, matches, semantic_reasons = semantic
                reasons = semantic_reasons
            if conflicts:
                status = "contradicted"
                reasons.extend(conflicts)
            kind = claim_kind(plain, _action_candidate(plain, decision.drugs))
            if method == "structured_study_field" and status == "supported":
                kind = "study_observation"
            applicability = assess_applicability(plain, patient_context, kind)
            if applicability.get(
                "patient_interpretation"
            ) == "inr" and not applicability.get("inr_interpretation_supported"):
                status = "unsupported"
                reasons.append(
                    "Patient-specific INR interpretation is not established by the supplied value, indication and target range."
                )
            checks.append(
                {
                    "knowledge_basis": (
                        "background_knowledge"
                        if "general pharmacology knowledge" in plain.lower()
                        else (
                            "hypothesis"
                            if "hypothesis" in plain.lower()
                            else "retrieved_evidence_claim" if refs else "unspecified"
                        )
                    ),
                    "text": plain,
                    "claim_kind": kind,
                    "patient_applicability": applicability,
                    "source_matches": matches,
                    "verification_method": method,
                    "publication_state": "published",
                    "evidence_ids": refs,
                    "drug_scope": scope,
                    "status": status,
                    "reasons": reasons,
                }
            )
    return checks
