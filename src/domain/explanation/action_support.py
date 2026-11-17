"""Conservative publication checks, not semantic or clinical validation.

Only complete, cited source passages can pass the action gate. This deliberately
withholds paraphrased advice until a stronger applicability verifier is available.
"""

from dataclasses import dataclass, field
import re
import unicodedata
from src.domain.explanation.claim_types import is_study_observation, DIRECTIVE_PATTERN
from src.domain.explanation.applicability import (
    assess_applicability,
    patient_causal_interpretation,
)
from src.domain.explanation.meaning import verify_meaning

from src.domain.explanation.passages import (
    match_sources,
    units,
    CONDITION,
    plain as source_plain,
    guidance_units,
)

REFERENCE = re.compile(r"\bev_[A-Za-z0-9_]+\b")
_CITATION = re.compile(r"\[[^\]\n]*\bev_[^\]\n]*\]")
_ACTION = re.compile(
    DIRECTIVE_PATTERN + r"|\b(?:administer\w*|dose|dosage|dosing|bedtime|nightly)\b|"
    r"\b(?:start|stop|hold|take|use|increase|reduce|decrease)\b.{0,55}"
    r"\b(?:treatment|therapy|medicine|medication|drug|tablet|mg|mcg)\b|"
    r"\b(?:daily|weekly|monthly|twice|thrice|every\s+\w+\s+(?:day|hour))\b|"
    r"\d\s*(?:mg|mcg|µg|units?|IU)\b|"
    r"(?:^|[.!?:]\s+)(?:reduce|increase|decrease|lower|raise|use)\b",
    re.I,
)
WITHHELD = (
    "Recommendation withheld: directly applicable source support could not be established. "
    "This is a limitation of this assessment, not evidence that no action is needed."
)
REVIEW_FAILED = (
    "Clinical explanation withheld: automated review did not complete. "
    "Retrieved evidence remains available, but this run did not produce a reviewed recommendation."
)
PATIENT_WITHHELD = "Patient interpretation withheld: supplied context and source support do not establish this conclusion."
INTERPRETATION_WITHHELD = (
    "Interpretation withheld: cited evidence does not establish this conclusion."
)


@dataclass(frozen=True)
class ActionSupportResult:
    text: str
    status: str
    findings: list[dict] = field(default_factory=list)
    supported_count: int = 0

    def summary(self):
        return {
            "status": self.status,
            "withheld_count": len(self.findings),
            "source_matched_count": self.supported_count,
            "limitation": "Complete source statements and medication scope matched; not clinical validation. Unmatched management advice is withheld.",
        }


def _plain(text):
    return source_plain(text)


def _normalized(text):
    return (
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))
        .strip()
        .lower()
        .rstrip(".")
    )


def _evidence_limitation(text):
    return bool(
        re.fullmatch(
            r"No (?:specific )?(?:numeric guidance|monitoring schedule|dose guidance|"
            r"dose recommendation|dose adjustment data|management recommendation|supporting evidence) "
            r"(?:was|is|were|are) (?:supplied|provided|available|found|retrieved)"
            r"(?: (?:by|in|from) the (?:sources|dataset|retrieved evidence))?\.?",
            text,
            re.I,
        )
    )


def _action_candidate(text, drugs):
    if is_study_observation(text):
        return False
    if _ACTION.search(text):
        return True
    # Direct verbs followed by a named drug must not require a generic noun
    # such as "dose". This leaves ordinary "increases exposure" biology intact.
    return any(
        re.search(
            r"\b(?:reduce|increase|decrease|lower|raise|use)\s+(?:the\s+)?"
            + re.escape(drug)
            + r"(?!\w)(?!\s+(?:exposure|clearance|metabolism|bioavailability|concentration|effect)\b)",
            text,
            re.I,
        )
        for drug in drugs
    )


def _supported(unit, cards, drugs, *, actions=True):
    refs = REFERENCE.findall(unit)
    if not refs or any(ref not in cards for ref in refs):
        return False
    plain = _plain(unit)
    mentioned = {
        drug
        for drug in drugs
        if re.search(r"(?<!\w)" + re.escape(drug) + r"(?!\w)", unit, re.I)
    }
    if not mentioned:
        return False  # "the dose" must not silently acquire another drug's scope.
    for ref in refs:
        card = cards[ref]
        if not actions and (
            card.evidence_grade
            not in {"label", "guideline", "clinical_study", "pk_study"}
            or card.source_type
            in {"mechanism_engine", "reasoning_engine", "normalized_context"}
        ):
            continue
        scope = set(card.drug_scope)
        if not scope or not scope.issubset(set(drugs)):
            continue
        if match_sources(plain, [card], mentioned, actions=actions):
            return True
    return False


def enforce_action_support(
    text, evidence_cards, drugs, *, review_status=None, patient_context=None
):
    if review_status == "review_failed":
        return ActionSupportResult(
            REVIEW_FAILED,
            "review_failed",
            [
                {
                    "reason": "Automated review failed; original explanation retained in audit."
                }
            ],
        )
    cards = {card.evidence_id: card for card in evidence_cards}
    output, findings = [], []
    in_actions, supported = False, 0
    for line in (
        unit for original in text.splitlines() for unit in guidance_units(original)
    ):
        heading = re.match(r"^\s*#{1,6}\s+(.+)", line)
        if heading:
            in_actions = bool(
                re.search(
                    r"\b(?:monitor(?:ing)?|actions?|management|recommendations?|dose|dosing)\b",
                    heading[1],
                    re.I,
                )
            )
            standard_heading = heading[1].strip().lower() in {
                "bottom line",
                "interaction mechanism",
                "clinical concern",
                "monitoring & actions",
                "evidence limitations",
                "direct answer",
                "what to monitor",
                "evidence boundary",
                "recommendations",
                "management",
            }
            if not standard_heading and _action_candidate(heading[1], drugs):
                findings.append(
                    {"text": line, "reason": "Unverified recommendation in a heading."}
                )
                output.append(WITHHELD)
            else:
                output.append(line)
            continue
        if not line.strip():
            output.append(line)
            continue
        # Preserve whole qualified quotations; split only independent sentences.
        candidates = (
            [line]
            if _supported(line, cards, drugs)
            or _supported(line, cards, drugs, actions=False)
            or CONDITION.search(_plain(units(line)[0]))
            or any(
                re.match(r"(?:This|These|Those|Only|Except|However)\b", _plain(u), re.I)
                for u in units(line)[1:]
            )
            else units(line)
        )
        for unit in candidates:
            if _evidence_limitation(_plain(unit)):
                output.append(unit)
                continue
            applicability = assess_applicability(_plain(unit), patient_context)
            meaning = verify_meaning(
                _plain(unit),
                [cards[r] for r in REFERENCE.findall(unit) if r in cards],
                drugs,
            )
            if (
                meaning
                and meaning[0] == "unsupported"
                and meaning[1] == "absence_check"
            ):
                findings.append(
                    {
                        "text": unit,
                        "reason": "A heuristic or missing evidence does not establish absence of pharmacodynamic interaction.",
                    }
                )
                if not output or output[-1] != INTERPRETATION_WITHHELD:
                    output.append(INTERPRETATION_WITHHELD)
                continue
            inr_unestablished = applicability.get(
                "patient_interpretation"
            ) == "inr" and not applicability.get("inr_interpretation_supported")
            patient_inference = patient_causal_interpretation(
                _plain(unit), patient_context
            )
            if inr_unestablished or (
                patient_inference and not _supported(unit, cards, drugs, actions=False)
            ):
                findings.append(
                    {
                        "text": unit,
                        "evidence_ids": REFERENCE.findall(unit),
                        "reason": "Patient interpretation is not established by supplied context and scoped source support.",
                        "applicability": applicability,
                    }
                )
                if not output or output[-1] != PATIENT_WITHHELD:
                    output.append(PATIENT_WITHHELD)
                continue
            if in_actions or _action_candidate(_plain(unit), drugs):
                applicability = assess_applicability(
                    _plain(unit), patient_context, "prescribing_advice"
                )
                if (
                    patient_context is not None
                    and applicability["status"] == "condition_conflict"
                ):
                    findings.append(
                        {
                            "text": unit,
                            "evidence_ids": REFERENCE.findall(unit),
                            "reason": "Source condition conflicts with supplied patient data.",
                            "applicability": applicability,
                        }
                    )
                    if not output or output[-1] != WITHHELD:
                        output.append(WITHHELD)
                    continue
                if _supported(unit, cards, drugs):
                    supported += 1
                    output.append(unit)
                else:
                    findings.append(
                        {
                            "text": unit,
                            "evidence_ids": REFERENCE.findall(unit),
                            "reason": "No complete, scoped statement in cited label/guideline evidence.",
                        }
                    )
                    if not output or output[-1] != WITHHELD:
                        output.append(WITHHELD)
            else:
                output.append(unit)
    status = (
        "recommendations_withheld"
        if findings
        else "source_text_matched" if supported else "no_actionable_recommendations"
    )
    return ActionSupportResult("\n".join(output), status, findings, supported)
