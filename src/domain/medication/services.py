from __future__ import annotations

import re

from src.domain.medication.entities import MedicationConcept, concept_id_for


_SPACE_RE = re.compile(r"\s+")


def normalize_medication_text(raw_text: str) -> str:
    cleaned = _SPACE_RE.sub(" ", str(raw_text or "").strip())
    return cleaned.lower()


def normalize_medication(raw_text: str) -> MedicationConcept:
    normalized = normalize_medication_text(raw_text)
    confidence = "exact_text" if normalized == str(raw_text or "").strip().lower() else "normalized_text"
    payload = {"normalized_name": normalized, "ingredients": [normalized]}
    return MedicationConcept(
        concept_id=concept_id_for(payload),
        raw_text=str(raw_text or "").strip(),
        normalized_name=normalized,
        ingredients=[normalized] if normalized else [],
        confidence=confidence if normalized else "unresolved",
    )


def medication_set_key(concepts: list[MedicationConcept]) -> str:
    ids = sorted(concept.concept_id for concept in concepts)
    return "medset:" + ":".join(ids)

