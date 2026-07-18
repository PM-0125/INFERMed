from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

NormalizationConfidence = Literal["exact_text", "normalized_text", "alias_match", "combination_product", "ambiguous", "unresolved"]


@dataclass(frozen=True)
class Ingredient:
    name: str
    identifiers: dict[str, str] = field(default_factory=dict)
    source: str = "local_identity_rules"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoseRouteForm:
    dose: str | None = None
    route: str | None = None
    form: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizationCandidate:
    normalized_name: str
    ingredients: list[str]
    confidence: NormalizationConfidence
    identifiers: dict[str, str] = field(default_factory=dict)
    provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    ingredient_details: list[Ingredient] = field(default_factory=list)
    dose_route_form: DoseRouteForm | None = None
    aliases: list[str] = field(default_factory=list)
    candidates: list[NormalizationCandidate] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    ambiguity_reason: str | None = None
    confidence: NormalizationConfidence = "normalized_text"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def concept_id_for(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "med_" + hashlib.sha256(encoded).hexdigest()[:16]
