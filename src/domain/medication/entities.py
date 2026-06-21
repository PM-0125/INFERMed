from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

NormalizationConfidence = Literal["exact_text", "normalized_text", "unresolved"]


@dataclass(frozen=True)
class MedicationConcept:
    """Canonical representation of a user-entered medication.

    This is intentionally conservative for the first migration step. It
    normalizes names and creates stable concept IDs without pretending we have
    RxNorm/UNII/brand resolution yet.
    """

    concept_id: str
    raw_text: str
    normalized_name: str
    ingredients: list[str]
    identifiers: dict[str, str] = field(default_factory=dict)
    confidence: NormalizationConfidence = "normalized_text"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def concept_id_for(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "med_" + hashlib.sha256(encoded).hexdigest()[:16]

