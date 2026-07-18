from __future__ import annotations

from src.domain.evidence.entities import EvidenceCard, EvidenceGrade

EVIDENCE_GRADE_ORDER: tuple[EvidenceGrade, ...] = (
    "label",
    "guideline",
    "clinical_study",
    "pk_study",
    "mechanistic",
    "pharmacovigilance_signal",
    "inferred_hypothesis",
    "insufficient",
)


def evidence_grade_rank(grade: str) -> int:
    try:
        return EVIDENCE_GRADE_ORDER.index(grade)  # lower is stronger
    except ValueError:
        return len(EVIDENCE_GRADE_ORDER)


def strongest_evidence_grade(cards: list[EvidenceCard]) -> EvidenceGrade:
    grades = {card.evidence_grade for card in cards}
    for grade in EVIDENCE_GRADE_ORDER:
        if grade in grades:
            return grade
    return "insufficient"


def rank_evidence_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    return sorted(
        cards,
        key=lambda card: (
            evidence_grade_rank(card.evidence_grade),
            card.source_name.lower(),
            card.claim_type.lower(),
            card.claim_text.lower(),
        ),
    )


def evidence_caveats(cards: list[EvidenceCard], *, limit: int = 16) -> list[str]:
    rows: list[str] = []
    for card in cards:
        rows.extend(str(item).strip() for item in card.limitations if str(item).strip())
    return list(dict.fromkeys(rows))[:limit]
