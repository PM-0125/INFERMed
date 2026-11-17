"""Bounded, auditable proposition checks; not general-purpose entailment.

No similarity threshold can authorize a recommendation. Unsupported grammar,
missing source fields and unrecognized relationships remain unresolved.
"""

import json
import re
from src.domain.explanation.claim_types import is_study_observation
from src.domain.explanation.passages import (
    normalized,
    plain,
    source_passages,
    passage_covers_scope,
    match_sources,
)


def _match(card, section, text, context=None):
    return dict(
        evidence_id=card.evidence_id,
        source_name=card.source_name,
        section=section,
        text=text,
        context=context or text,
        source_url=(card.payload.get("provenance") or card.provenance).get(
            "source_url"
        ),
    )


def verify_meaning(text, cards, scope):
    """Return status, method, matches, reasons for a recognized proposition."""
    target = normalized(plain(text))
    from src.domain.explanation.study_fields import renal_study_fields

    if re.fullmatch(
        r"the .+ pharmacokinetic study in \d+ subjects aged \d+ years or older reported creatinine clearance of .+",
        target,
    ):
        for card in cards:
            if not set(card.drug_scope).issubset(set(scope)):
                continue
            for fact in renal_study_fields(card):
                if normalized(fact["text"]) == target:
                    return (
                        "supported",
                        "structured_study_field",
                        [
                            _match(
                                card,
                                fact["section"],
                                fact["text"],
                                fact["source_context"],
                            )
                        ],
                        [
                            "Cohort, age boundary, endpoint, value and unit matched a complete study source unit. This is not a patient measurement or prescription."
                        ],
                    )
        return (
            "unresolved",
            "structured_study_field",
            [],
            [
                "The exact reported study field and population were not established from a complete cited unit."
            ],
        )
    relation = re.fullmatch(
        r"([\w -]+?) (?:is a (strong|moderate|weak) inhibitor of|(strongly|moderately|weakly) inhibits) (cyp\w+)",
        target,
    )
    if relation:
        drug, adjective, adverb, enzyme = relation.groups()
        strength = (
            adjective
            or {"strongly": "strong", "moderately": "moderate", "weakly": "weak"}[
                adverb
            ]
        )
        facts = []
        for card in cards:
            if card.claim_type != "mechanism_reference" or drug not in [
                d.lower() for d in card.drug_scope
            ]:
                continue
            for entry in card.payload.get("matches", []):
                row = entry.get("row") or {}
                if str(row.get("Enzyme", "")).lower() != enzyme:
                    continue
                for heading, value in row.items():
                    field = re.fullmatch(
                        r"(Strong|Moderate|Weak)(?: index)? inhibitors", heading, re.I
                    )
                    if field and re.search(
                        r"(?<!\w)" + re.escape(drug) + r"(?!\w)", str(value), re.I
                    ):
                        facts.append(
                            (
                                field[1].lower(),
                                _match(
                                    card,
                                    "enzyme_reference",
                                    json.dumps(row, ensure_ascii=False),
                                ),
                            )
                        )
        if facts:
            strengths = {s for s, _ in facts}
            if len(strengths) != 1:
                return (
                    "unresolved",
                    "structured_relation",
                    [m for _, m in facts],
                    ["Cited structured references disagree on inhibition strength."],
                )
            agrees = strength in strengths
            return (
                "supported" if agrees else "contradicted",
                "structured_relation",
                [m for _, m in facts],
                [
                    f"Structured reference identifies {drug} as a {facts[0][0]} inhibitor of {enzyme.upper()}; claim says {strength}."
                ],
            )
    if is_study_observation(text):
        # A single observed, redundant wording variation. All numbers, polarity,
        # population, temporal conditions and remaining words must match exactly.
        def study_form(value):
            return re.sub(
                r"(\b\d+(?:\.\d+)?\s*(?:mg|mcg)) administered (?=daily\b)",
                r"\1 ",
                normalized(value),
            )

        for card in cards:
            if card.evidence_grade not in {"label", "clinical_study", "pk_study"}:
                continue
            for passage in source_passages(card):
                if (
                    passage_covers_scope(card, passage, scope)
                    and is_study_observation(passage["text"])
                    and study_form(plain(text)) == study_form(passage["text"])
                ):
                    return (
                        "supported",
                        "study_regimen_equivalence",
                        [
                            _match(
                                card,
                                passage["section"],
                                passage["text"],
                                passage["context"],
                            )
                        ],
                        [
                            "Study regimen wording differs only by the redundant administered-before-daily construction; population, quantities and qualifications match. Not prescribing advice."
                        ],
                    )
    if re.search(
        r"\bno (?:significant |obvious |additional |additive )?(?:pharmacodynamic|pd) (?:overlap|interaction|synergy)|\b(?:interaction is purely|risk is driven solely by) pharmacokinetic\b",
        target,
    ):
        eligible = [
            c
            for c in cards
            if c.evidence_grade in {"label", "guideline", "clinical_study", "pk_study"}
            and c.source_type
            not in {"mechanism_engine", "reasoning_engine", "normalized_context"}
        ]
        if not match_sources(plain(text), eligible, scope):
            return (
                "unsupported",
                "absence_check",
                [],
                [
                    "A mechanism heuristic or missing evidence does not establish absence of pharmacodynamic interaction."
                ],
            )
    return None
